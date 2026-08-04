from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

EMERGENCY_BINDING_SCHEMA_VERSION = "emergency-offline-authorization-binding-1.0"
EMERGENCY_EVENT_TYPE = "EMERGENCY_OFFLINE_AUTHORIZED"
EMERGENCY_JOB_TYPE = "SYSTEM_EMERGENCY_SET_OFFLINE"
EMERGENCY_FLAG_NAME = "automatic_emergency_offline"
EMERGENCY_APPROVAL_POLICY = "SYSTEM_EMERGENCY_V1"
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_UI_AUTOMATION_JOB_TYPES = (
    "FULL_MARKET_SCAN",
    "LISTING_STATUS_SCAN",
    "ONLINE_PULSE",
    "ORDER_SCAN",
    "POST_CUTOFF_PULSE",
    "PRE_CUTOFF_FULL_SCAN",
)
_BINDING_FIELDS = frozenset(
    {
        "schema_version",
        "authorization_id",
        "authorization_event_id",
        "authorization_evidence_sha256",
        "automation_run_id",
        "source_task_id",
        "incident_id",
        "review_task_id",
        "policy_version",
        "feature_flag_job_id",
        "platform_name",
        "internal_sku",
        "expires_at",
        "runtime_db_path",
    }
)


class EmergencyOfflineFenceError(ValueError):
    """The emergency authorization no longer permits a platform click."""


def build_emergency_authorization_binding(connection, *, task_id: str) -> dict[str, str]:
    """Build the immutable request binding from an authorized Runtime task."""

    task = connection.execute(
        "SELECT * FROM tasks WHERE task_id = ?",
        (str(task_id),),
    ).fetchone()
    if task is None:
        raise EmergencyOfflineFenceError("EMERGENCY_TASK_MISSING")
    trace = _json_object(task["decision_trace_json"])
    event_id = str(trace.get("authorization_event_id") or "")
    event = connection.execute(
        "SELECT * FROM automation_run_events WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if event is None or str(event["event_type"]) != EMERGENCY_EVENT_TYPE:
        raise EmergencyOfflineFenceError("EMERGENCY_AUTHORIZATION_EVENT_MISSING")
    payload = _json_object(event["payload_json"])
    stored_hash = str(payload.pop("authorization_evidence_sha256", ""))
    if not _SHA256_RE.fullmatch(stored_hash) or stored_hash != _sha256_json(payload):
        raise EmergencyOfflineFenceError("EMERGENCY_AUTHORIZATION_HASH_INVALID")
    binding = {
        "schema_version": EMERGENCY_BINDING_SCHEMA_VERSION,
        "authorization_id": str(payload.get("authorization_id") or ""),
        "authorization_event_id": event_id,
        "authorization_evidence_sha256": stored_hash,
        "automation_run_id": str(event["run_id"] or ""),
        "source_task_id": str(task["task_id"] or ""),
        "incident_id": str(payload.get("incident_id") or ""),
        "review_task_id": str(payload.get("review_task_id") or ""),
        "policy_version": str(payload.get("policy_version") or ""),
        "feature_flag_job_id": str(payload.get("feature_flag_job_id") or ""),
        "platform_name": str(payload.get("platform_name") or ""),
        "internal_sku": str(payload.get("internal_sku") or ""),
        "expires_at": str(payload.get("expires_at") or ""),
        "runtime_db_path": str(payload.get("runtime_db_path") or ""),
    }
    validate_emergency_authorization_binding(binding)
    _assert_equal(trace, "authorization_id", binding["authorization_id"])
    _assert_equal(trace, "authorization_event_id", binding["authorization_event_id"])
    _assert_equal(
        trace,
        "authorization_evidence_sha256",
        binding["authorization_evidence_sha256"],
    )
    _assert_equal(trace, "automation_run_id", binding["automation_run_id"])
    _assert_equal(trace, "incident_id", binding["incident_id"])
    _assert_equal(trace, "review_task_id", binding["review_task_id"])
    _assert_equal(trace, "feature_flag_job_id", binding["feature_flag_job_id"])
    _assert_equal(trace, "runtime_db_path", binding["runtime_db_path"])
    return binding


def validate_emergency_authorization_binding(binding: Any) -> None:
    if not isinstance(binding, dict) or set(binding) != _BINDING_FIELDS:
        raise EmergencyOfflineFenceError("EMERGENCY_BINDING_INVALID")
    if binding.get("schema_version") != EMERGENCY_BINDING_SCHEMA_VERSION:
        raise EmergencyOfflineFenceError("EMERGENCY_BINDING_SCHEMA_INVALID")
    for name in (
        "authorization_id",
        "authorization_event_id",
        "automation_run_id",
        "source_task_id",
        "incident_id",
        "review_task_id",
        "policy_version",
        "feature_flag_job_id",
    ):
        if not _ID_RE.fullmatch(str(binding.get(name) or "")):
            raise EmergencyOfflineFenceError("EMERGENCY_BINDING_ID_INVALID:" + name)
    if not _SHA256_RE.fullmatch(
        str(binding.get("authorization_evidence_sha256") or "")
    ):
        raise EmergencyOfflineFenceError("EMERGENCY_BINDING_HASH_INVALID")
    if not str(binding.get("platform_name") or "").strip():
        raise EmergencyOfflineFenceError("EMERGENCY_BINDING_PLATFORM_INVALID")
    if not str(binding.get("internal_sku") or "").strip():
        raise EmergencyOfflineFenceError("EMERGENCY_BINDING_SKU_INVALID")
    _aware_datetime(binding.get("expires_at"), "expires_at")
    runtime_path = Path(str(binding.get("runtime_db_path") or ""))
    if not runtime_path.is_absolute() or runtime_path.suffix.lower() not in {
        ".db",
        ".sqlite",
        ".sqlite3",
    }:
        raise EmergencyOfflineFenceError("EMERGENCY_BINDING_RUNTIME_DB_INVALID")


def revalidate_emergency_offline_facts(
    connection,
    *,
    binding: dict[str, Any],
    now: datetime,
    allowed_task_statuses: Iterable[str],
    operation_id: str = "",
    require_active_lock: bool = False,
) -> dict[str, Any]:
    """Re-read all mutable veto facts in the caller's SQLite transaction."""

    validate_emergency_authorization_binding(binding)
    current = _aware_datetime(now, "now")
    if current >= _aware_datetime(binding["expires_at"], "expires_at"):
        raise EmergencyOfflineFenceError("EMERGENCY_AUTHORIZATION_EXPIRED")

    event = connection.execute(
        "SELECT * FROM automation_run_events WHERE event_id = ?",
        (binding["authorization_event_id"],),
    ).fetchone()
    if (
        event is None
        or str(event["event_type"]) != EMERGENCY_EVENT_TYPE
        or str(event["run_id"]) != binding["automation_run_id"]
    ):
        raise EmergencyOfflineFenceError("EMERGENCY_AUTHORIZATION_EVENT_DRIFTED")
    evidence = _json_object(event["payload_json"])
    evidence_hash = str(evidence.pop("authorization_evidence_sha256", ""))
    if (
        evidence_hash != binding["authorization_evidence_sha256"]
        or evidence_hash != _sha256_json(evidence)
    ):
        raise EmergencyOfflineFenceError("EMERGENCY_AUTHORIZATION_HASH_DRIFTED")
    for field in (
        "authorization_id",
        "incident_id",
        "review_task_id",
        "policy_version",
        "feature_flag_job_id",
        "platform_name",
        "internal_sku",
        "expires_at",
        "runtime_db_path",
    ):
        if str(evidence.get(field) or "") != str(binding[field]):
            raise EmergencyOfflineFenceError("EMERGENCY_AUTHORIZATION_SCOPE_DRIFTED:" + field)
    if str(evidence.get("action") or "") != "set_offline":
        raise EmergencyOfflineFenceError("EMERGENCY_AUTHORIZATION_ACTION_INVALID")

    task = connection.execute(
        "SELECT * FROM tasks WHERE task_id = ?",
        (binding["source_task_id"],),
    ).fetchone()
    allowed = {str(value).lower() for value in allowed_task_statuses}
    if (
        task is None
        or str(task["task_status"]).lower() not in allowed
        or str(task["origin_type"]) != "SYSTEM_EMERGENCY"
        or str(task["approval_policy"]) != EMERGENCY_APPROVAL_POLICY
        or str(task["action_type"]) != "set_offline"
        or str(task["target_status"] or "") != "offline"
        or str(task["platform_name"] or "") != binding["platform_name"]
        or str(task["internal_sku"] or "") != binding["internal_sku"]
        or str(task["policy_version"] or "") != binding["policy_version"]
    ):
        raise EmergencyOfflineFenceError("EMERGENCY_TASK_DRIFTED")

    incident = connection.execute(
        "SELECT * FROM operational_incidents WHERE incident_id = ?",
        (binding["incident_id"],),
    ).fetchone()
    if (
        incident is None
        or str(incident["category"]) != "PRICE_ANOMALY"
        or str(incident["severity"]) != "S4"
        or str(incident["incident_status"]) != "AUTO_PROTECTING"
        or str(incident["platform_name"] or "") != binding["platform_name"]
        or str(incident["subject_key"] or "") != binding["internal_sku"]
    ):
        raise EmergencyOfflineFenceError("EMERGENCY_INCIDENT_NO_LONGER_ACTIVE")

    review = connection.execute(
        "SELECT * FROM review_tasks WHERE review_task_id = ?",
        (binding["review_task_id"],),
    ).fetchone()
    if (
        review is None
        or str(review["review_status"]) != "pending"
        or str(review["scope_key"]) != binding["incident_id"]
    ):
        raise EmergencyOfflineFenceError("EMERGENCY_REVIEW_ALREADY_RESOLVED")

    policy = connection.execute(
        """
        SELECT 1 FROM emergency_offline_policies
        WHERE policy_version = ? AND platform_name = ?
          AND emergency_ratio = '0.80'
          AND approved_at IS NOT NULL AND retired_at IS NULL
        """,
        (binding["policy_version"], binding["platform_name"]),
    ).fetchone()
    if policy is None:
        raise EmergencyOfflineFenceError("EMERGENCY_POLICY_INACTIVE")

    flag = connection.execute(
        "SELECT * FROM automation_jobs WHERE job_id = ?",
        (binding["feature_flag_job_id"],),
    ).fetchone()
    config = _json_object(flag["config_json"] if flag is not None else "{}")
    if (
        flag is None
        or str(flag["job_type"]) != EMERGENCY_JOB_TYPE
        or int(flag["enabled"]) != 1
        or config.get(EMERGENCY_FLAG_NAME) is not True
        or str(config.get("platform_name") or "") != binding["platform_name"]
    ):
        raise EmergencyOfflineFenceError("EMERGENCY_FEATURE_FLAG_DISABLED")

    manual = connection.execute(
        """
        SELECT 1 FROM tasks
        WHERE platform_name = ? AND internal_sku = ?
          AND origin_type = 'MANUAL'
          AND action_type IN ('update_price', 'set_offline')
          AND task_status IN ('pending', 'running', 'manual_review')
        LIMIT 1
        """,
        (binding["platform_name"], binding["internal_sku"]),
    ).fetchone()
    if manual is not None:
        raise EmergencyOfflineFenceError("EMERGENCY_MANUAL_TASK_TAKES_PRIORITY")

    placeholders = ",".join("?" for _ in _UI_AUTOMATION_JOB_TYPES)
    active_ui = connection.execute(
        f"""
        SELECT 1 FROM automation_runs
        WHERE job_type IN ({placeholders})
          AND run_status = 'RUNNING'
          AND julianday(lease_expires_at) > julianday(?)
        LIMIT 1
        """,
        (*_UI_AUTOMATION_JOB_TYPES, current.astimezone(timezone.utc).isoformat()),
    ).fetchone()
    if active_ui is not None:
        raise EmergencyOfflineFenceError("EMERGENCY_AUTOMATION_UI_BUSY")

    if require_active_lock:
        lock = connection.execute(
            """
            SELECT 1 FROM shadowbot_write_locks
            WHERE operation_id = ? AND status = 'ACTIVE'
            """,
            (str(operation_id),),
        ).fetchone()
        if lock is None:
            raise EmergencyOfflineFenceError("EMERGENCY_WRITE_LOCK_DRIFTED")
    return evidence


def _assert_equal(source: dict[str, Any], field: str, expected: str) -> None:
    if str(source.get(field) or "") != expected:
        raise EmergencyOfflineFenceError("EMERGENCY_TASK_TRACE_DRIFTED:" + field)


def _aware_datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise EmergencyOfflineFenceError(
                "EMERGENCY_DATETIME_INVALID:" + field
            ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EmergencyOfflineFenceError("EMERGENCY_DATETIME_NAIVE:" + field)
    return parsed


def _json_object(value: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _sha256_json(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

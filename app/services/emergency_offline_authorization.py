from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.automation_ui_channel import has_active_automation_ui_run
from app.enums import (
    IncidentCategory,
    IncidentStatus,
    SellerPhase,
    TaskActionType,
    TaskOriginType,
    TaskStatus,
)
from app.exceptions import ValidationError
from app.models import Task
from app.repositories.sqlite_runtime_repository import (
    SQLiteRuntimeRepository,
    _row_to_task,
)
from app.services.emergency_offline_shadow import EmergencyOfflineShadowService
from app.shadowbot_contract_primitives import contract_identity_key

EMERGENCY_JOB_TYPE = "SYSTEM_EMERGENCY_SET_OFFLINE"
EMERGENCY_FLAG_NAME = "automatic_emergency_offline"
EMERGENCY_EVENT_TYPE = "EMERGENCY_OFFLINE_AUTHORIZED"
EMERGENCY_APPROVAL_POLICY = "SYSTEM_EMERGENCY_V1"
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")


@dataclass(frozen=True, slots=True)
class EmergencyOfflineAuthorizationResult:
    authorization_id: str
    authorization_event_id: str
    automation_run_id: str
    task: Task
    evidence: dict[str, object]
    evidence_sha256: str
    replayed: bool


class EmergencyOfflineAuthorizationService:
    """The only service allowed to create a SYSTEM_EMERGENCY task."""

    def __init__(self, runtime_repository: SQLiteRuntimeRepository) -> None:
        self.runtime_repository = runtime_repository
        self.shadow = EmergencyOfflineShadowService(runtime_repository)

    def authorize(
        self,
        *,
        authorization_id: str,
        incident_id: str,
        review_task_id: str,
        products_path: Path,
        feature_flag_job_id: str,
        authorized_at: datetime,
        expires_at: datetime,
        initial_observation_id: str | None = None,
        failure_injector=None,
    ) -> EmergencyOfflineAuthorizationResult:
        normalized_id = _require_id(authorization_id, "authorization_id")
        flag_job_id = _require_id(feature_flag_job_id, "feature_flag_job_id")
        now = _aware_text(authorized_at, "authorized_at")
        expiry = _aware_text(expires_at, "expires_at")
        if expires_at <= authorized_at:
            raise ValueError("expires_at must be after authorized_at")

        shadow = self.shadow.evaluate(
            evaluation_id=f"authorization-shadow:{normalized_id}",
            incident_id=incident_id,
            review_task_id=review_task_id,
            products_path=products_path,
            evaluated_at=authorized_at,
            initial_observation_id=initial_observation_id,
        )
        if not shadow.eligible_without_feature_flag:
            raise ValidationError(
                "emergency authorization facts are not eligible: "
                + ",".join(shadow.blockers)
            )
        if shadow.blockers not in {(), ("FEATURE_FLAG_DISABLED",)}:
            raise ValidationError("shadow decision has unexpected blockers")
        if not shadow.pulse_run_id or not shadow.second_observation_id:
            raise ValidationError("qualified Pulse evidence is incomplete")

        event_id = _stable_id("AUTO-EVENT-EMERGENCY", normalized_id)
        task_id = _stable_id("TASK-EMERGENCY", normalized_id)
        connection = self.runtime_repository.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_event = connection.execute(
                "SELECT * FROM automation_run_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing_event is not None:
                result = self._replay_result(
                    connection,
                    authorization_id=normalized_id,
                    event_id=event_id,
                    task_id=task_id,
                    expected_run_id=shadow.pulse_run_id,
                )
                connection.rollback()
                return result

            incident = connection.execute(
                "SELECT * FROM operational_incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if (
                incident is None
                or str(incident["category"]) != IncidentCategory.PRICE_ANOMALY.value
                or str(incident["severity"]) != "S4"
                or str(incident["incident_status"])
                not in {IncidentStatus.OPEN.value, IncidentStatus.WAITING_HUMAN.value}
            ):
                raise ValidationError("S4 price Incident is no longer active")
            platform_name = str(incident["platform_name"] or "")
            internal_sku = str(incident["subject_key"] or "")
            platform_trade_date = str(incident["platform_trade_date"] or "")
            if not platform_name or not internal_sku or not platform_trade_date:
                raise ValidationError("Incident scope is incomplete")

            review = connection.execute(
                "SELECT * FROM review_tasks WHERE review_task_id = ?",
                (review_task_id,),
            ).fetchone()
            if (
                review is None
                or str(review["scope_key"]) != incident_id
                or str(review["review_status"]) != "pending"
                or str(review["platform_name"] or "") != platform_name
                or str(review["internal_sku"] or "") != internal_sku
            ):
                raise ValidationError("human Review already resolved or drifted")

            flag_job = connection.execute(
                "SELECT * FROM automation_jobs WHERE job_id = ?",
                (flag_job_id,),
            ).fetchone()
            flag_config = _json_object(
                flag_job["config_json"] if flag_job is not None else "{}"
            )
            if (
                flag_job is None
                or str(flag_job["job_type"]) != EMERGENCY_JOB_TYPE
                or int(flag_job["enabled"]) != 1
                or flag_config.get(EMERGENCY_FLAG_NAME) is not True
                or str(flag_config.get("platform_name") or "") != platform_name
            ):
                raise ValidationError("automatic emergency offline flag is disabled")

            policy = connection.execute(
                """
                SELECT * FROM emergency_offline_policies
                WHERE policy_version = ? AND platform_name = ?
                  AND approved_at IS NOT NULL AND retired_at IS NULL
                  AND emergency_ratio = '0.80'
                """,
                (shadow.policy_version, platform_name),
            ).fetchone()
            if policy is None:
                raise ValidationError("emergency policy is no longer active")

            pulse_run = connection.execute(
                "SELECT * FROM automation_runs WHERE run_id = ?",
                (shadow.pulse_run_id,),
            ).fetchone()
            if (
                pulse_run is None
                or str(pulse_run["run_status"]) not in {"SUCCESS", "MERGED"}
                or str(pulse_run["platform_name"]) != platform_name
                or str(pulse_run["platform_trade_date"]) != platform_trade_date
            ):
                raise ValidationError("qualified Pulse Run changed")
            observation_run_id = shadow.pulse_run_id
            if str(pulse_run["run_status"]) == "MERGED":
                merged_target = connection.execute(
                    """
                    SELECT parent.run_id
                    FROM automation_run_links AS link
                    JOIN automation_runs AS parent
                      ON parent.run_id = link.parent_run_id
                    WHERE link.child_run_id = ?
                      AND link.relation_type = 'MERGED_RUN'
                      AND parent.run_status = 'SUCCESS'
                    """,
                    (shadow.pulse_run_id,),
                ).fetchone()
                if merged_target is None:
                    raise ValidationError("merged Pulse target is no longer successful")
                observation_run_id = str(merged_target["run_id"])

            first = _observation(connection, shadow.first_observation_id)
            second = _observation(connection, shadow.second_observation_id)
            for label, observation in (("first", first), ("second", second)):
                if (
                    observation is None
                    or str(observation["platform_name"]) != platform_name
                    or str(observation["platform_trade_date"]) != platform_trade_date
                    or str(observation["internal_sku"] or "") != internal_sku
                    or str(observation["mapping_status"]) != "VERIFIED"
                    or int(observation["observed_online"]) != 1
                ):
                    raise ValidationError(f"{label} observation changed")
            if str(second["automation_run_id"]) != observation_run_id:
                raise ValidationError("second observation is not bound to Pulse Run")
            if str(first["observation_item_id"]) == str(second["observation_item_id"]):
                raise ValidationError("Pulse did not produce an independent observation")
            if str(second["observed_price"]) != str(shadow.second_observed_price):
                raise ValidationError("second observation price changed")

            initial_notification = _initial_notification(
                connection,
                incident_id=incident_id,
                review_task_id=review_task_id,
            )
            if initial_notification is None:
                raise ValidationError("initial Review notification is not SENT")
            sent_at = datetime.fromisoformat(str(initial_notification["sent_at"]))
            pulse_scheduled_for = datetime.fromisoformat(
                str(pulse_run["scheduled_for"])
            )
            second_scan_started_at = datetime.fromisoformat(
                str(second["scan_started_at"])
            )
            if (
                pulse_scheduled_for <= sent_at
                or second_scan_started_at <= sent_at
            ):
                raise ValidationError("qualified Pulse no longer follows notification")

            if has_active_automation_ui_run(connection, now=authorized_at):
                raise ValidationError("Automation UI channel is busy")
            _assert_no_manual_conflict(
                connection,
                platform_name=platform_name,
                internal_sku=internal_sku,
            )
            write_identity_key = contract_identity_key(
                platform_name,
                internal_sku,
                None,
                None,
            )
            lock = connection.execute(
                """
                SELECT 1 FROM shadowbot_write_locks
                WHERE write_identity_key = ?
                  AND status IN ('ACTIVE', 'UNKNOWN', 'REVIEW_BLOCKED')
                """,
                (write_identity_key,),
            ).fetchone()
            if lock is not None:
                raise ValidationError("shared write lock blocks authorization")

            evidence = {
                "authorization_id": normalized_id,
                "incident_id": incident_id,
                "policy_version": shadow.policy_version,
                "policy_canonical_sha256": shadow.policy_canonical_sha256,
                "platform_name": platform_name,
                "internal_sku": internal_sku,
                "platform_trade_date": platform_trade_date,
                "first_observation_id": shadow.first_observation_id,
                "first_observation_sha256": str(first["evidence_sha256"]),
                "first_observed_price": str(first["observed_price"]),
                "second_observation_id": shadow.second_observation_id,
                "second_observation_sha256": str(second["evidence_sha256"]),
                "second_observed_price": str(second["observed_price"]),
                "base_cost": str(shadow.base_cost),
                "base_cost_source_ref": shadow.base_cost_source_ref,
                "emergency_ratio": "0.80",
                "emergency_threshold": str(shadow.emergency_threshold),
                "initial_outbox_id": str(initial_notification["notification_id"]),
                "initial_outbox_sent_at": str(initial_notification["sent_at"]),
                "decision_window_started_at": str(initial_notification["sent_at"]),
                "automatic_eligibility_reached_at": str(
                    second["scan_completed_at"]
                ),
                "completed_pulse_run_id": shadow.pulse_run_id,
                "completed_observation_run_id": observation_run_id,
                "review_task_id": review_task_id,
                "review_status": "pending",
                "feature_flag_job_id": flag_job_id,
                "feature_flag_name": EMERGENCY_FLAG_NAME,
                "feature_flag_read_result": True,
                "expected_current_online_state": "online",
                "action": TaskActionType.SET_OFFLINE.value,
                "authorized_at": now,
                "expires_at": expiry,
                "runtime_db_path": str(self.runtime_repository.db_path.resolve()),
            }
            evidence_sha256 = _sha256_json(evidence)
            event_payload = {
                **evidence,
                "authorization_evidence_sha256": evidence_sha256,
            }
            connection.execute(
                """
                INSERT INTO automation_run_events(
                    event_id, run_id, event_type, from_status, to_status,
                    payload_json, created_at
                ) VALUES (?, ?, ?, 'SUCCESS', 'SUCCESS', ?, ?)
                """,
                (
                    event_id,
                    shadow.pulse_run_id,
                    EMERGENCY_EVENT_TYPE,
                    _json_text(event_payload),
                    now,
                ),
            )
            if failure_injector:
                failure_injector("after_authorization_event")

            task = Task(
                task_id=task_id,
                internal_sku=internal_sku,
                platform_name=platform_name,
                action_type=TaskActionType.SET_OFFLINE,
                priority=100,
                task_status=TaskStatus.PENDING,
                created_at=authorized_at,
                target_status="offline",
                decision_trace={
                    "authorization_id": normalized_id,
                    "authorization_event_id": event_id,
                    "authorization_evidence_sha256": evidence_sha256,
                    "automation_run_id": shadow.pulse_run_id,
                    "runtime_db_path": str(self.runtime_repository.db_path.resolve()),
                    "review_task_id": review_task_id,
                    "incident_id": incident_id,
                    "feature_flag_job_id": flag_job_id,
                },
                result_message="紧急下架已授权，等待复用 v5 执行链",
                required_by=expires_at,
                trade_date=date.fromisoformat(platform_trade_date),
                origin_type=TaskOriginType.SYSTEM_EMERGENCY,
                origin_ref_id=f"emergency:{normalized_id}",
                approval_policy=EMERGENCY_APPROVAL_POLICY,
                policy_version=shadow.policy_version,
                platform_trade_date=date.fromisoformat(platform_trade_date),
                seller_operation_date=date.fromisoformat(
                    str(incident["seller_operation_date"])
                ),
                seller_phase=SellerPhase(str(pulse_run["seller_phase"])),
                time_policy_version=str(pulse_run["time_policy_version"]),
                scope_type="sku",
                scope_key=internal_sku,
                dedupe_key=f"emergency:{normalized_id}",
                expires_at=expires_at,
                updated_at=authorized_at,
            )
            if SQLiteRuntimeRepository._insert_tasks_on_connection(
                connection,
                [task],
            ) != 1:
                raise ValidationError("SYSTEM_EMERGENCY task was not inserted")
            if failure_injector:
                failure_injector("after_emergency_task")

            _transition_incident_to_auto_protecting(
                connection,
                incident=incident,
                event_key=f"emergency-authorization:{normalized_id}",
                task_id=task_id,
                authorization_event_id=event_id,
                occurred_at=now,
            )
            if failure_injector:
                failure_injector("after_incident_transition")
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

        stored = self.runtime_repository.get_task(task_id)
        if stored is None:
            raise RuntimeError("authorized task disappeared after commit")
        return EmergencyOfflineAuthorizationResult(
            authorization_id=normalized_id,
            authorization_event_id=event_id,
            automation_run_id=shadow.pulse_run_id,
            task=stored,
            evidence=evidence,
            evidence_sha256=evidence_sha256,
            replayed=False,
        )

    def _replay_result(
        self,
        connection,
        *,
        authorization_id: str,
        event_id: str,
        task_id: str,
        expected_run_id: str,
    ) -> EmergencyOfflineAuthorizationResult:
        event = connection.execute(
            "SELECT * FROM automation_run_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        task_row = connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if (
            event is None
            or task_row is None
            or str(event["event_type"]) != EMERGENCY_EVENT_TYPE
            or str(event["run_id"]) != expected_run_id
            or str(task_row["origin_type"]) != TaskOriginType.SYSTEM_EMERGENCY.value
            or str(task_row["origin_ref_id"]) != f"emergency:{authorization_id}"
        ):
            raise ValidationError("authorization replay conflicts with stored facts")
        payload = _json_object(event["payload_json"])
        evidence_sha256 = str(payload.pop("authorization_evidence_sha256", ""))
        if evidence_sha256 != _sha256_json(payload):
            raise ValidationError("stored authorization evidence hash is invalid")
        task = _row_to_task(task_row)
        return EmergencyOfflineAuthorizationResult(
            authorization_id=authorization_id,
            authorization_event_id=event_id,
            automation_run_id=expected_run_id,
            task=task,
            evidence=payload,
            evidence_sha256=evidence_sha256,
            replayed=True,
        )


def _observation(connection, observation_id: str):
    return connection.execute(
        """
        SELECT item.*, batch.platform_name, batch.automation_run_id,
               batch.content_sha256, batch.scan_started_at,
               batch.scan_completed_at,
               batch.batch_status, batch.scope_complete,
               batch.end_marker_verified
        FROM product_observation_items AS item
        JOIN product_observation_batches AS batch
          ON batch.observation_batch_id = item.observation_batch_id
        WHERE item.observation_item_id = ?
          AND batch.batch_status = 'ACCEPTED'
          AND batch.scope_complete = 1
          AND batch.end_marker_verified = 1
        """,
        (observation_id,),
    ).fetchone()


def _initial_notification(connection, *, incident_id: str, review_task_id: str):
    events = connection.execute(
        """
        SELECT event_payload_json FROM operational_incident_events
        WHERE incident_id = ? AND event_type = 'REVIEW_RECORDED'
        ORDER BY occurred_at, event_id
        """,
        (incident_id,),
    ).fetchall()
    notification_id = ""
    for row in events:
        payload = _json_object(row["event_payload_json"])
        if str(payload.get("review_task_id") or "") == review_task_id:
            notification_id = str(payload.get("notification_id") or "")
            break
    if not notification_id:
        return None
    return connection.execute(
        """
        SELECT notification_id, sent_at FROM notification_outbox
        WHERE notification_id = ? AND status = 'SENT' AND sent_at IS NOT NULL
        """,
        (notification_id,),
    ).fetchone()


def _assert_no_manual_conflict(connection, *, platform_name: str, internal_sku: str) -> None:
    row = connection.execute(
        """
        SELECT task_id FROM tasks
        WHERE platform_name = ? AND internal_sku = ?
          AND origin_type = 'MANUAL'
          AND action_type IN ('update_price', 'set_offline')
          AND task_status IN ('pending', 'running', 'manual_review')
        LIMIT 1
        """,
        (platform_name, internal_sku),
    ).fetchone()
    if row is not None:
        raise ValidationError("higher-priority manual task blocks authorization")


def _transition_incident_to_auto_protecting(
    connection,
    *,
    incident,
    event_key: str,
    task_id: str,
    authorization_event_id: str,
    occurred_at: str,
) -> None:
    from_status = str(incident["incident_status"])
    updated = connection.execute(
        """
        UPDATE operational_incidents
        SET incident_status = 'AUTO_PROTECTING', updated_at = ?
        WHERE incident_id = ? AND incident_status = ?
        """,
        (occurred_at, incident["incident_id"], from_status),
    )
    if updated.rowcount != 1:
        raise ValidationError("Incident changed during emergency authorization")
    payload = {
        "authorization_event_id": authorization_event_id,
        "task_id": task_id,
    }
    connection.execute(
        """
        INSERT INTO operational_incident_events(
            event_id, event_key, incident_id, event_type, occurred_at,
            source_type, source_ref_id, from_status, to_status, severity,
            event_payload_json, created_at
        ) VALUES (?, ?, ?, 'TASK_RECORDED', ?, 'EMERGENCY_AUTHORIZATION', ?,
                  ?, 'AUTO_PROTECTING', ?, ?, ?)
        """,
        (
            _stable_id("incident-event", event_key),
            event_key,
            incident["incident_id"],
            occurred_at,
            task_id,
            from_status,
            incident["severity"],
            _json_text(payload),
            occurred_at,
        ),
    )


def _require_id(value: str, field: str) -> str:
    normalized = str(value).strip()
    if not _ID_RE.fullmatch(normalized):
        raise ValueError(f"{field} is invalid")
    return normalized


def _aware_text(value: datetime, field: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.isoformat()


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_object(value: object) -> dict[str, object]:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_json_text(value).encode("utf-8")).hexdigest()

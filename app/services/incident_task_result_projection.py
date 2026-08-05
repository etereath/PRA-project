from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any


_SUCCESS_OUTCOMES = frozenset({"VERIFIED", "ALREADY_APPLIED"})
_FAILED_OUTCOMES = frozenset({"FAILED", "NOT_APPLIED", "NOT_ATTEMPTED"})
_UNKNOWN_OUTCOMES = frozenset(
    {"UNKNOWN", "NEEDS_RECONCILIATION", "PARTIALLY_APPLIED"}
)


def project_manual_incident_task_result(
    connection,
    *,
    source_task_id: str,
    operation_id: str,
    outcome: str,
    result_id: str,
    occurred_at: str,
) -> bool:
    """Project a formal Incident Review task result in the Importer transaction."""

    normalized = str(outcome or "").strip().upper()
    if normalized not in _SUCCESS_OUTCOMES | _FAILED_OUTCOMES | _UNKNOWN_OUTCOMES:
        return False
    task = connection.execute(
        "SELECT * FROM tasks WHERE task_id = ?",
        (source_task_id,),
    ).fetchone()
    if task is None or str(task["origin_type"]) != "MANUAL":
        return False
    trace = _json_object(task["decision_trace_json"])
    incident_id = str(trace.get("incident_id") or "").strip()
    review_task_id = str(trace.get("review_task_id") or "").strip()
    if not incident_id or not review_task_id:
        return False
    review = connection.execute(
        "SELECT scope_key FROM review_tasks WHERE review_task_id = ?",
        (review_task_id,),
    ).fetchone()
    incident = connection.execute(
        "SELECT * FROM operational_incidents WHERE incident_id = ?",
        (incident_id,),
    ).fetchone()
    if (
        review is None
        or incident is None
        or str(review["scope_key"]) != incident_id
    ):
        raise ValueError("manual Incident task result binding is invalid")

    event_key = (
        "incident-task-result:"
        + source_task_id
        + ":"
        + result_id
        + ":"
        + operation_id
    )
    existing = connection.execute(
        "SELECT event_payload_json FROM operational_incident_events WHERE event_key = ?",
        (event_key,),
    ).fetchone()
    if existing is not None:
        existing_payload = _json_object(existing["event_payload_json"])
        if (
            str(existing_payload.get("task_id") or "") != source_task_id
            or str(existing_payload.get("review_task_id") or "") != review_task_id
            or str(existing_payload.get("operation_id") or "") != operation_id
            or str(existing_payload.get("operation_result") or "") != normalized
        ):
            raise ValueError("manual Incident task result replay conflicts")
        return True

    current_status = str(incident["incident_status"])
    latest_event = connection.execute(
        """
        SELECT occurred_at FROM operational_incident_events
        WHERE incident_id = ?
        ORDER BY julianday(occurred_at) DESC, occurred_at DESC, event_id DESC
        LIMIT 1
        """,
        (incident_id,),
    ).fetchone()
    projection_is_current = (
        latest_event is None
        or _parse_datetime(occurred_at)
        > _parse_datetime(str(latest_event["occurred_at"]))
    )
    success = normalized in _SUCCESS_OUTCOMES
    target_status = (
        "RESOLVED"
        if success
        and projection_is_current
        and current_status != "CLOSED"
        else current_status
    )
    if target_status == "RESOLVED" and current_status != "RESOLVED":
        connection.execute(
            """
            UPDATE operational_incidents
            SET incident_status = 'RESOLVED', resolved_at = ?, updated_at = ?
            WHERE incident_id = ? AND incident_status = ?
            """,
            (occurred_at, occurred_at, incident_id, current_status),
        )
    elif projection_is_current:
        connection.execute(
            "UPDATE operational_incidents SET updated_at = ? WHERE incident_id = ?",
            (occurred_at, incident_id),
        )

    notification_kind = (
        "incident_task_success"
        if success
        else "incident_task_failed"
        if normalized in _FAILED_OUTCOMES
        else "incident_task_unknown"
    )
    event_type = "RECOVERY_RECORDED" if success else "TASK_RECORDED"
    payload = {
        "task_id": source_task_id,
        "review_task_id": review_task_id,
        "operation_id": operation_id,
        "operation_result": normalized,
        "action_type": str(task["action_type"]),
        "projection_applied": projection_is_current,
        "notification_kind": notification_kind,
    }
    connection.execute(
        """
        INSERT INTO operational_incident_events(
            event_id, event_key, incident_id, event_type, occurred_at,
            source_type, source_ref_id, from_status, to_status, severity,
            event_payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, 'SHADOWBOT_RESULT_IMPORTER', ?, ?, ?, ?, ?, ?)
        """,
        (
            _stable_id("incident-event", event_key),
            event_key,
            incident_id,
            event_type,
            occurred_at,
            source_task_id,
            current_status,
            target_status,
            str(incident["severity"]),
            _json_text(payload),
            occurred_at,
        ),
    )
    _insert_notification(
        connection,
        incident_id=incident_id,
        review_task_id=review_task_id,
        task_id=source_task_id,
        event_key=event_key,
        notification_kind=notification_kind,
        outcome=normalized,
        occurred_at=occurred_at,
    )
    return True


def _insert_notification(
    connection,
    *,
    incident_id: str,
    review_task_id: str,
    task_id: str,
    event_key: str,
    notification_kind: str,
    outcome: str,
    occurred_at: str,
) -> None:
    notification_key = notification_kind + ":" + event_key
    notification_id = _stable_id("NOTIFY", notification_key)
    title = {
        "incident_task_success": "处置任务已完成",
        "incident_task_failed": "处置任务失败",
        "incident_task_unknown": "处置结果待确认",
    }[notification_kind]
    message = {
        "incident_task_success": "人工复核生成的处置任务已验证完成。",
        "incident_task_failed": "人工复核生成的处置任务未完成，请继续处理。",
        "incident_task_unknown": "平台结果尚不能确认，请按现有对账流程处理。",
    }[notification_kind]
    recipient_type = (
        os.getenv("DEFAULT_NOTIFICATION_RECIPIENT_TYPE", "role").strip() or "role"
    )
    recipient_ref = (
        os.getenv("DEFAULT_NOTIFICATION_RECIPIENT", "operations").strip()
        or "operations"
    )
    channel = os.getenv("DEFAULT_NOTIFICATION_CHANNEL", "").strip().lower()
    connection.execute(
        """
        INSERT OR IGNORE INTO notification_outbox(
            notification_id, notification_key, notification_type,
            related_task_id, related_review_task_id, recipient_type,
            recipient_ref, channel, priority, payload_json, status,
            attempt_count, max_attempts, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 100, ?, 'PENDING', 0, 3, ?, ?)
        """,
        (
            notification_id,
            notification_key,
            notification_kind,
            task_id,
            review_task_id,
            recipient_type,
            recipient_ref,
            channel or "unconfigured",
            _json_text(
                {
                    "incident_id": incident_id,
                    "review_task_id": review_task_id,
                    "task_id": task_id,
                    "outcome": outcome,
                    "title": title,
                    "message": message,
                }
            ),
            occurred_at,
            occurred_at,
        ),
    )


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _json_object(value: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Incident task result time must be timezone-aware")
    return parsed

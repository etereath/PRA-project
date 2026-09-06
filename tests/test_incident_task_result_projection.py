from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.enums import TaskActionType, TaskOriginType, TaskStatus
from app.models import Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.incident_task_result_projection import (
    project_manual_incident_task_result,
)


NOW = datetime(2026, 8, 4, 13, 0, tzinfo=timezone.utc)


def _repository(tmp_path) -> SQLiteRuntimeRepository:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    repository.init_schema()
    task = Task(
        task_id="TASK-INCIDENT-MANUAL",
        internal_sku="SKU-1",
        platform_name="platform",
        action_type=TaskActionType.SET_OFFLINE,
        priority=0,
        task_status=TaskStatus.RUNNING,
        created_at=NOW,
        target_status="offline",
        decision_trace={"incident_id": "INCIDENT-1", "review_task_id": "REVIEW-1"},
        origin_type=TaskOriginType.MANUAL,
        origin_ref_id="incident-review:REVIEW-1",
        expires_at=NOW + timedelta(hours=1),
    )
    repository.insert_task(task)
    with repository.connect_write() as connection, connection:
        connection.execute(
            """
            INSERT INTO operational_incidents(
                incident_id, dedupe_key, category, source_type, source_ref_id,
                severity, incident_status, blocks_finalization, platform_name,
                platform_trade_date, seller_operation_date, subject_type,
                subject_key, title, description, first_detected_at,
                last_detected_at, resolved_at, occurrence_count, created_at,
                updated_at
            ) VALUES (
                'INCIDENT-1', 'incident-1', 'PRICE_ANOMALY', 'FIXTURE', 'OBS-1',
                'S4', 'WAITING_HUMAN', 1, 'platform', '2026-08-04',
                '2026-08-04', 'internal_sku', 'SKU-1', 'price anomaly', '',
                ?, ?, NULL, 1, ?, ?
            )
            """,
            (NOW.isoformat(), NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            """
            INSERT INTO review_tasks(
                review_task_id, trade_date, scope_type, scope_key, dedupe_key,
                source_task_id, review_type, review_status, internal_sku,
                platform_name, reason, review_payload_json,
                resolution_payload_json, required_by, created_at, updated_at
            ) VALUES (
                'REVIEW-1', '2026-08-04', 'incident', 'INCIDENT-1', 'review-1',
                NULL, 'emergency_protection', 'approved', 'SKU-1', 'platform',
                'price anomaly', '{}', '{}', ?, ?, ?
            )
            """,
            (
                (NOW + timedelta(hours=1)).isoformat(),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
    return repository


@pytest.mark.parametrize(
    ("outcome", "expected_status", "notification_kind"),
    [
        ("VERIFIED", "RESOLVED", "incident_task_success"),
        ("ALREADY_APPLIED", "RESOLVED", "incident_task_success"),
        ("FAILED", "WAITING_HUMAN", "incident_task_failed"),
        ("NOT_APPLIED", "WAITING_HUMAN", "incident_task_failed"),
        ("NOT_ATTEMPTED", "WAITING_HUMAN", "incident_task_failed"),
        ("UNKNOWN", "WAITING_HUMAN", "incident_task_unknown"),
        ("NEEDS_RECONCILIATION", "WAITING_HUMAN", "incident_task_unknown"),
        ("PARTIALLY_APPLIED", "WAITING_HUMAN", "incident_task_unknown"),
    ],
)
def test_manual_incident_result_projection_and_notification_are_atomic(
    tmp_path,
    outcome: str,
    expected_status: str,
    notification_kind: str,
) -> None:
    repository = _repository(tmp_path)
    occurred_at = (NOW + timedelta(minutes=1)).isoformat()
    with repository.connect_write() as connection, connection:
        assert project_manual_incident_task_result(
            connection,
            source_task_id="TASK-INCIDENT-MANUAL",
            operation_id="OP-1",
            outcome=outcome,
            result_id="RESULT-1",
            occurred_at=occurred_at,
        )

    with repository.connect_read() as connection:
        assert connection.execute(
            "SELECT incident_status FROM operational_incidents WHERE incident_id = 'INCIDENT-1'"
        ).fetchone()[0] == expected_status
        assert connection.execute(
            "SELECT COUNT(*) FROM operational_incident_events WHERE source_ref_id = 'TASK-INCIDENT-MANUAL'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT notification_type FROM notification_outbox"
        ).fetchone()[0] == notification_kind


def test_manual_incident_result_projection_replay_is_exact(tmp_path) -> None:
    repository = _repository(tmp_path)
    occurred_at = (NOW + timedelta(minutes=1)).isoformat()
    with repository.connect_write() as connection, connection:
        for _ in range(2):
            assert project_manual_incident_task_result(
                connection,
                source_task_id="TASK-INCIDENT-MANUAL",
                operation_id="OP-1",
                outcome="VERIFIED",
                result_id="RESULT-1",
                occurred_at=occurred_at,
            )

    with repository.connect_read() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM operational_incident_events WHERE source_ref_id = 'TASK-INCIDENT-MANUAL'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM notification_outbox WHERE related_task_id = 'TASK-INCIDENT-MANUAL'"
        ).fetchone()[0] == 1


def test_manual_incident_result_projection_replay_conflict_is_rejected(
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    occurred_at = (NOW + timedelta(minutes=1)).isoformat()
    with repository.connect_write() as connection, connection:
        assert project_manual_incident_task_result(
            connection,
            source_task_id="TASK-INCIDENT-MANUAL",
            operation_id="OP-1",
            outcome="VERIFIED",
            result_id="RESULT-1",
            occurred_at=occurred_at,
        )
        with pytest.raises(ValueError, match="replay conflicts"):
            project_manual_incident_task_result(
                connection,
                source_task_id="TASK-INCIDENT-MANUAL",
                operation_id="OP-1",
                outcome="FAILED",
                result_id="RESULT-1",
                occurred_at=occurred_at,
            )


def test_success_result_does_not_reopen_closed_incident(tmp_path) -> None:
    repository = _repository(tmp_path)
    with repository.connect_write() as connection, connection:
        connection.execute(
            "UPDATE operational_incidents SET incident_status = 'CLOSED', "
            "resolved_at = ? WHERE incident_id = 'INCIDENT-1'",
            (NOW.isoformat(),),
        )
        assert project_manual_incident_task_result(
            connection,
            source_task_id="TASK-INCIDENT-MANUAL",
            operation_id="OP-1",
            outcome="VERIFIED",
            result_id="RESULT-1",
            occurred_at=(NOW + timedelta(minutes=1)).isoformat(),
        )

    with repository.connect_read() as connection:
        assert connection.execute(
            "SELECT incident_status FROM operational_incidents "
            "WHERE incident_id = 'INCIDENT-1'"
        ).fetchone()[0] == "CLOSED"

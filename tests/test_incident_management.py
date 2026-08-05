from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from app.enums import IncidentCategory, IncidentEventType, IncidentStatus
from app.exceptions import IncidentIdempotencyConflictError, IncidentTransitionError
from app.repositories.operational_incident_repository import (
    OperationalIncidentRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.incident_management import (
    IncidentDetection,
    IncidentManagementService,
)

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def runtime_repository(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    repository.init_schema()
    return repository


def detection(event_key: str, *, occurred_at: datetime = NOW) -> IncidentDetection:
    return IncidentDetection(
        event_key=event_key,
        category=IncidentCategory.WORKER_UNAVAILABLE,
        source_type="AUTOMATION_RUN",
        source_ref_id="run-001",
        severity="S3",
        blocks_finalization=True,
        platform_name="synthetic-platform",
        platform_trade_date=date(2026, 8, 2),
        seller_operation_date=date(2026, 8, 2),
        subject_type="worker",
        subject_key="primary-worker",
        title="Worker unavailable",
        description="Synthetic fixture only",
        occurred_at=occurred_at,
        reason="heartbeat_stale",
        payload={"heartbeat_age_seconds": 45},
    )


def test_detection_creates_incident_and_event_atomically(runtime_repository):
    service = IncidentManagementService(runtime_repository)

    result = service.detect(detection("detect-001"))

    assert result.replayed is False
    assert result.incident.incident_status is IncidentStatus.OPEN
    assert result.incident.occurrence_count == 1
    assert result.event.event_type is IncidentEventType.DETECTED
    assert service.list_events(result.incident.incident_id) == [result.event]


def test_exact_detection_replay_does_not_increment_occurrence(runtime_repository):
    service = IncidentManagementService(runtime_repository)
    first = service.detect(detection("detect-001"))

    replay = service.detect(detection("detect-001"))

    assert replay.replayed is True
    assert replay.incident.incident_id == first.incident.incident_id
    assert replay.incident.occurrence_count == 1
    assert len(service.list_events(first.incident.incident_id)) == 1


def test_same_event_key_with_different_content_conflicts(runtime_repository):
    service = IncidentManagementService(runtime_repository)
    service.detect(detection("detect-001"))
    changed = replace(
        detection("detect-001"),
        payload={"heartbeat_age_seconds": 90},
    )

    with pytest.raises(IncidentIdempotencyConflictError):
        service.detect(changed)


def test_redetection_reuses_active_incident_and_reopens_resolved(runtime_repository):
    service = IncidentManagementService(runtime_repository)
    first = service.detect(detection("detect-001"))
    resolved = service.transition(
        first.incident.incident_id,
        to_status=IncidentStatus.RESOLVED,
        event_key="resolve-001",
        occurred_at=NOW + timedelta(minutes=1),
        source_type="RECOVERY_CHECK",
        reason="heartbeat_restored",
    )
    assert resolved.incident.resolved_at is not None

    redetected = service.detect(
        detection("detect-002", occurred_at=NOW + timedelta(minutes=2))
    )

    assert redetected.incident.incident_id == first.incident.incident_id
    assert redetected.incident.incident_status is IncidentStatus.OPEN
    assert redetected.incident.resolved_at is None
    assert redetected.incident.occurrence_count == 2
    assert redetected.event.event_type is IncidentEventType.REDETECTED
    assert redetected.event.from_status is IncidentStatus.RESOLVED
    assert redetected.event.to_status is IncidentStatus.OPEN


def test_late_detection_is_appended_without_reopening_resolved_projection(
    runtime_repository,
):
    service = IncidentManagementService(runtime_repository)
    first = service.detect(detection("detect-t1", occurred_at=NOW))
    resolved = service.transition(
        first.incident.incident_id,
        to_status=IncidentStatus.RESOLVED,
        event_key="resolve-t3",
        occurred_at=NOW + timedelta(minutes=3),
        source_type="RECOVERY_CHECK",
    )

    late = service.detect(
        detection("detect-t2-late", occurred_at=NOW + timedelta(minutes=2))
    )

    assert late.incident.incident_id == first.incident.incident_id
    assert late.incident.incident_status is IncidentStatus.RESOLVED
    assert late.incident.resolved_at == resolved.incident.resolved_at
    assert late.incident.last_detected_at == first.incident.last_detected_at
    assert late.incident.updated_at == resolved.incident.updated_at
    assert late.incident.occurrence_count == 1
    assert late.event.event_type is IncidentEventType.REDETECTED
    assert late.event.from_status is IncidentStatus.RESOLVED
    assert late.event.to_status is IncidentStatus.RESOLVED


def test_older_detection_after_newer_detection_does_not_regress_projection(
    runtime_repository,
):
    service = IncidentManagementService(runtime_repository)
    first = service.detect(detection("detect-t1", occurred_at=NOW))
    newest = service.detect(
        detection("detect-t4", occurred_at=NOW + timedelta(minutes=4))
    )

    late = service.detect(
        detection("detect-t2-late", occurred_at=NOW + timedelta(minutes=2))
    )

    assert late.incident.incident_id == first.incident.incident_id
    assert late.incident.last_detected_at == newest.incident.last_detected_at
    assert late.incident.updated_at == newest.incident.updated_at
    assert late.incident.occurrence_count == 2
    assert len(service.list_events(first.incident.incident_id)) == 3


def test_late_status_transition_is_audited_without_changing_current_projection(
    runtime_repository,
):
    service = IncidentManagementService(runtime_repository)
    first = service.detect(detection("detect-t1", occurred_at=NOW))
    resolved = service.transition(
        first.incident.incident_id,
        to_status=IncidentStatus.RESOLVED,
        event_key="resolve-t3",
        occurred_at=NOW + timedelta(minutes=3),
        source_type="RECOVERY_CHECK",
    )

    late = service.transition(
        first.incident.incident_id,
        to_status=IncidentStatus.WAITING_HUMAN,
        event_key="wait-t2-late",
        occurred_at=NOW + timedelta(minutes=2),
        source_type="INCIDENT_SERVICE",
    )

    assert late.incident.incident_status is IncidentStatus.RESOLVED
    assert late.incident.updated_at == resolved.incident.updated_at
    assert late.event.from_status is IncidentStatus.RESOLVED
    assert late.event.to_status is IncidentStatus.RESOLVED
    assert late.event.event_payload["requested_to_status"] == "WAITING_HUMAN"


def test_detection_after_closed_incident_uses_event_time_to_distinguish_recurrence(
    runtime_repository,
):
    service = IncidentManagementService(runtime_repository)
    first = service.detect(detection("detect-t1", occurred_at=NOW))
    service.transition(
        first.incident.incident_id,
        to_status=IncidentStatus.RESOLVED,
        event_key="resolve-t3",
        occurred_at=NOW + timedelta(minutes=3),
        source_type="RECOVERY_CHECK",
    )
    closed = service.transition(
        first.incident.incident_id,
        to_status=IncidentStatus.CLOSED,
        event_key="close-t4",
        occurred_at=NOW + timedelta(minutes=4),
        source_type="OPERATIONS",
    )

    late = service.detect(
        detection("detect-t2-late", occurred_at=NOW + timedelta(minutes=2))
    )
    recurrence = service.detect(
        detection("detect-t5", occurred_at=NOW + timedelta(minutes=5))
    )

    assert late.incident.incident_id == closed.incident.incident_id
    assert late.incident.incident_status is IncidentStatus.CLOSED
    assert recurrence.incident.incident_id != closed.incident.incident_id
    assert recurrence.incident.incident_status is IncidentStatus.OPEN


def test_closed_incident_recurrence_creates_new_incident(runtime_repository):
    service = IncidentManagementService(runtime_repository)
    first = service.detect(detection("detect-001"))
    service.transition(
        first.incident.incident_id,
        to_status=IncidentStatus.RESOLVED,
        event_key="resolve-001",
        occurred_at=NOW + timedelta(minutes=1),
        source_type="RECOVERY_CHECK",
    )
    service.transition(
        first.incident.incident_id,
        to_status=IncidentStatus.CLOSED,
        event_key="close-001",
        occurred_at=NOW + timedelta(minutes=2),
        source_type="OPERATIONS",
    )

    recurrence = service.detect(
        detection("detect-002", occurred_at=NOW + timedelta(minutes=3))
    )

    assert recurrence.incident.incident_id != first.incident.incident_id
    assert recurrence.incident.occurrence_count == 1
    assert recurrence.event.event_type is IncidentEventType.DETECTED


def test_ack_is_event_only_and_transition_replay_is_exact(runtime_repository):
    service = IncidentManagementService(runtime_repository)
    first = service.detect(detection("detect-001"))
    waiting = service.transition(
        first.incident.incident_id,
        to_status=IncidentStatus.WAITING_HUMAN,
        event_key="wait-001",
        occurred_at=NOW + timedelta(minutes=1),
        source_type="INCIDENT_SERVICE",
    )
    replay = service.transition(
        first.incident.incident_id,
        to_status=IncidentStatus.WAITING_HUMAN,
        event_key="wait-001",
        occurred_at=NOW + timedelta(minutes=1),
        source_type="INCIDENT_SERVICE",
    )
    ack = service.acknowledge(
        first.incident.incident_id,
        event_key="ack-001",
        occurred_at=NOW + timedelta(minutes=2),
        actor="farm-owner",
        note="received",
    )

    assert replay.replayed is True
    assert waiting.incident.incident_status is IncidentStatus.WAITING_HUMAN
    assert ack.incident.incident_status is IncidentStatus.WAITING_HUMAN
    assert ack.incident.occurrence_count == 1
    assert ack.event.event_type is IncidentEventType.ACK


def test_invalid_transition_and_database_failure_leave_no_partial_rows(
    runtime_repository,
):
    service = IncidentManagementService(runtime_repository)
    first = service.detect(detection("detect-001"))
    with pytest.raises(IncidentTransitionError):
        service.transition(
            first.incident.incident_id,
            to_status=IncidentStatus.CLOSED,
            event_key="close-invalid",
            occurred_at=NOW + timedelta(minutes=1),
            source_type="OPERATIONS",
        )

    repository = OperationalIncidentRepository(runtime_repository)

    def fail(point: str) -> None:
        if point == "after_incident_write":
            raise RuntimeError("synthetic failure")

    with pytest.raises(RuntimeError, match="synthetic failure"):
        repository.record_detection(
            event_key="detect-rollback",
            dedupe_key="rollback-dedupe",
            category=IncidentCategory.RUNTIME_STORAGE,
            source_type="SYSTEM_SMOKE",
            source_ref_id="fixture",
            severity="S2",
            blocks_finalization=True,
            platform_name=None,
            platform_trade_date=None,
            seller_operation_date=None,
            subject_type="runtime_db",
            subject_key="fixture-db",
            title="Synthetic rollback",
            description="",
            occurred_at=NOW,
            failure_injector=fail,
        )

    with runtime_repository.connect_read() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM operational_incidents WHERE dedupe_key = 'rollback-dedupe'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM operational_incident_events WHERE event_key = 'detect-rollback'"
            ).fetchone()[0]
            == 0
        )


def test_severity_and_recovery_are_append_only_events(runtime_repository):
    service = IncidentManagementService(runtime_repository)
    first = service.detect(detection("detect-001"))

    severity = service.change_severity(
        first.incident.incident_id,
        severity="S4",
        event_key="severity-001",
        occurred_at=NOW + timedelta(minutes=1),
        source_type="TRUSTED_EVALUATOR",
        reason="threshold_crossed",
    )
    recovery = service.record_recovery(
        first.incident.incident_id,
        event_key="recovery-001",
        occurred_at=NOW + timedelta(minutes=2),
        source_type="WORKER_WATCHDOG",
        payload={"heartbeat_restored": True},
    )

    assert severity.incident.severity == "S4"
    assert severity.event.event_type is IncidentEventType.SEVERITY_CHANGED
    assert recovery.incident.incident_status is IncidentStatus.OPEN
    assert recovery.event.event_type is IncidentEventType.RECOVERY_RECORDED
    assert recovery.incident.occurrence_count == 1


def test_late_severity_change_is_audited_without_regressing_current_projection(
    runtime_repository,
):
    service = IncidentManagementService(runtime_repository)
    first = service.detect(detection("detect-severity-t1", occurred_at=NOW))
    newest = service.change_severity(
        first.incident.incident_id,
        severity="S4",
        event_key="severity-t4",
        occurred_at=NOW + timedelta(minutes=4),
        source_type="TRUSTED_EVALUATOR",
        reason="current threshold",
    )

    late = service.change_severity(
        first.incident.incident_id,
        severity="S2",
        event_key="severity-t2-late",
        occurred_at=NOW + timedelta(minutes=2),
        source_type="TRUSTED_EVALUATOR",
        reason="delayed observation",
    )

    assert late.incident.severity == "S4"
    assert late.incident.updated_at == newest.incident.updated_at
    assert late.event.severity == "S4"
    assert late.event.event_payload["requested_to_severity"] == "S2"


@pytest.mark.parametrize("terminal_status", [IncidentStatus.RESOLVED, IncidentStatus.CLOSED])
def test_late_severity_change_does_not_mutate_resolved_or_closed_projection(
    runtime_repository,
    terminal_status,
):
    service = IncidentManagementService(runtime_repository)
    first = service.detect(detection("detect-terminal-severity", occurred_at=NOW))
    resolved = service.transition(
        first.incident.incident_id,
        to_status=IncidentStatus.RESOLVED,
        event_key="resolve-terminal-severity",
        occurred_at=NOW + timedelta(minutes=3),
        source_type="RECOVERY_CHECK",
    )
    current = resolved
    if terminal_status is IncidentStatus.CLOSED:
        current = service.transition(
            first.incident.incident_id,
            to_status=IncidentStatus.CLOSED,
            event_key="close-terminal-severity",
            occurred_at=NOW + timedelta(minutes=4),
            source_type="OPERATIONS",
        )

    late = service.change_severity(
        first.incident.incident_id,
        severity="S4",
        event_key=f"severity-late-{terminal_status.value.lower()}",
        occurred_at=NOW + timedelta(minutes=2),
        source_type="TRUSTED_EVALUATOR",
        reason="delayed escalation",
    )

    assert late.incident.incident_status is terminal_status
    assert late.incident.severity == current.incident.severity
    assert late.incident.updated_at == current.incident.updated_at
    assert late.event.event_payload["requested_to_severity"] == "S4"


def test_late_severity_change_exact_replay_is_idempotent(runtime_repository):
    service = IncidentManagementService(runtime_repository)
    first = service.detect(detection("detect-severity-replay", occurred_at=NOW))
    service.change_severity(
        first.incident.incident_id,
        severity="S4",
        event_key="severity-replay-newest",
        occurred_at=NOW + timedelta(minutes=4),
        source_type="TRUSTED_EVALUATOR",
        reason="current threshold",
    )
    kwargs = {
        "severity": "S2",
        "event_key": "severity-replay-late",
        "occurred_at": NOW + timedelta(minutes=2),
        "source_type": "TRUSTED_EVALUATOR",
        "reason": "delayed observation",
    }

    initial = service.change_severity(first.incident.incident_id, **kwargs)
    replay = service.change_severity(first.incident.incident_id, **kwargs)

    assert initial.replayed is False
    assert replay.replayed is True
    assert replay.event.event_id == initial.event.event_id
    assert replay.incident.severity == "S4"

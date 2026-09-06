from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.enums import AutomationRunStatus, IncidentCategory
from app.operations_web.auth import (
    Capability,
    Principal,
    PrincipalCapabilityBackend,
)
from app.repositories.automation_repository import AutomationRepository
from app.repositories.operational_incident_repository import (
    OperationalIncidentRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import AutomationSchedulePlanner, AutomationService
from app.services.maintenance_automation import (
    RELEASE_BACKUP_JOB_ID,
    build_maintenance_handlers,
)
from app.services.operations_maintenance import (
    OperationsMaintenanceApplicationService,
    OperationsMaintenanceError,
)


NOW = datetime(2026, 8, 13, 6, 30, tzinfo=UTC)


@pytest.fixture()
def maintenance(tmp_path: Path):
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    queue = tmp_path / "queue"
    for name in ("inbox", "working", "results", "archive", "control"):
        (queue / name).mkdir(parents=True, exist_ok=True)
    queue_heartbeat = queue / "control" / "pra_queue_services_heartbeat.json"
    queue_heartbeat.write_text(
        json.dumps(
            {
                "schema_version": "queue-services-heartbeat-1.0",
                "service": "shadowbot_queue_services",
                "status": "RUNNING",
                "updated_at": NOW.isoformat(),
                "notification_worker_enabled": True,
                "notification_channel": "fake",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    automation_heartbeat = tmp_path / "automation-heartbeat.json"
    automation_heartbeat.write_text(
        json.dumps(
            {
                "schema_version": "automation-heartbeat-1.0",
                "status": "RUNNING",
                "last_cycle_at": NOW.isoformat(),
                "worker_recovery_handler_registered": True,
                "release_backup_handler_registered": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = OperationsMaintenanceApplicationService(
        runtime,
        PrincipalCapabilityBackend(),
        queue_root=queue,
        platform_name="synthetic-platform",
        notification_channel="fake",
        automation_heartbeat=automation_heartbeat,
        clock=lambda: NOW,
    )
    admin = Principal("admin", frozenset({Capability.SYSTEM_ADMIN}))
    return service, runtime, queue, admin


def test_notification_test_only_enqueues_and_exact_replay_is_idempotent(maintenance) -> None:
    service, runtime, _, admin = maintenance

    first = service.request_notification_test(
        admin,
        idempotency_key="notification-test-001",
    )
    replay = service.request_notification_test(
        admin,
        idempotency_key="notification-test-001",
    )

    assert first.status == "QUEUED"
    assert first.replayed is False
    assert replay.reference_id == first.reference_id
    assert replay.replayed is True
    notifications = runtime.list_notification_outbox()
    assert len(notifications) == 1
    assert notifications[0].notification_type == "system_test"
    assert notifications[0].status == "PENDING"


def test_worker_recovery_reuses_incident_and_automation_without_host_action(maintenance) -> None:
    service, runtime, _, admin = maintenance

    first = service.request_worker_recovery(
        admin,
        idempotency_key="worker-recovery-001",
    )
    replay = service.request_worker_recovery(
        admin,
        idempotency_key="worker-recovery-001",
    )

    assert first.status == "SCHEDULED"
    assert replay.reference_id == first.reference_id
    assert replay.replayed is True
    incidents = OperationalIncidentRepository(runtime).list_active(
        category=IncidentCategory.WORKER_UNAVAILABLE
    )
    assert len(incidents) == 1
    assert incidents[0].occurrence_count == 1
    runs = AutomationRepository(runtime).list_runs()
    assert [run.run_id for run in runs] == [first.reference_id]
    assert runs[0].run_status is AutomationRunStatus.SCHEDULED


def test_healthy_worker_returns_no_action_and_writes_no_runtime_fact(maintenance) -> None:
    service, runtime, queue, admin = maintenance
    (queue / "heartbeat.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "updated_at": NOW.isoformat(),
                "worker_id": "synthetic-worker",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    receipt = service.request_worker_recovery(
        admin,
        idempotency_key="worker-recovery-healthy",
    )

    assert receipt.status == "NO_ACTION"
    assert AutomationRepository(runtime).list_runs() == []
    assert OperationalIncidentRepository(runtime).list_active() == []


def test_backup_is_manual_only_and_runs_through_automation_handler(maintenance) -> None:
    service, runtime, _, admin = maintenance
    receipt = service.request_backup(admin, idempotency_key="backup-request-001")
    automation = AutomationRepository(runtime)
    job = automation.get_job(RELEASE_BACKUP_JOB_ID)
    assert job is not None
    assert job.schedule_kind == "MANUAL_ONLY"

    scheduled = AutomationSchedulePlanner(automation).materialize(
        now=NOW,
        executable_job_types=(),
    )
    assert scheduled.created_run_ids == ()

    calls: list[str] = []

    def executor(backup_id: str):
        calls.append(backup_id)
        return {"backup_id": backup_id, "verified": True, "created": True}

    cycle = AutomationService(
        automation,
        handlers=build_maintenance_handlers(release_backup_executor=executor),
        owner_token="maintenance-test-owner",
        clock=lambda: NOW,
    ).run_cycle()

    assert cycle.completed_run_ids == (receipt.reference_id,)
    assert calls == [receipt.reference_id]
    finished = automation.get_run(receipt.reference_id)
    assert finished is not None
    assert finished.run_status is AutomationRunStatus.SUCCESS


def test_system_admin_capability_is_required_without_any_write(maintenance) -> None:
    service, runtime, _, _ = maintenance
    viewer = Principal("viewer", frozenset({Capability.VIEW_SYSTEM}))

    with pytest.raises(OperationsMaintenanceError, match="系统维护权限"):
        service.request_notification_test(
            viewer,
            idempotency_key="notification-test-denied",
        )

    assert runtime.list_notification_outbox() == []
    assert AutomationRepository(runtime).list_runs() == []


def test_missing_independent_carrier_rejects_intent_without_business_write(
    maintenance,
) -> None:
    service, runtime, queue, admin = maintenance
    (queue / "control" / "pra_queue_services_heartbeat.json").unlink()
    assert service.automation_heartbeat is not None
    service.automation_heartbeat.unlink()

    with pytest.raises(OperationsMaintenanceError, match="没有可用心跳"):
        service.request_notification_test(
            admin,
            idempotency_key="notification-test-no-worker",
        )
    with pytest.raises(OperationsMaintenanceError, match="没有可用心跳"):
        service.request_backup(admin, idempotency_key="backup-no-automation")

    assert runtime.list_notification_outbox() == []
    assert AutomationRepository(runtime).list_runs() == []

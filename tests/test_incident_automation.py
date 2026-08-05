from __future__ import annotations

from datetime import timedelta

import pytest

from app.enums import AutomationRunStatus
from app.repositories.automation_repository import AutomationRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import AutomationService
from app.services.incident_automation import (
    INCIDENT_NOTIFICATION_JOB_ID,
    INCIDENT_NOTIFICATION_MAINTENANCE,
    build_incident_notification_handlers,
    ensure_incident_notification_automation_job,
)
from app.services.incident_management import IncidentReviewService
from app.services.shadowbot_worker_recovery import (
    ShadowBotWorkerRecoveryCoordinator,
)
from tests.test_incident_review_service import (
    NOW,
    create_s4_incident,
    mark_notification,
)


@pytest.fixture
def runtime_repository(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "REVIEW_TOKEN_SECRET",
        "incident-automation-test-secret-at-least-32-bytes",
    )
    monkeypatch.setenv("MOBILE_REVIEW_BASE_URL", "https://example.test")
    monkeypatch.setenv("DEFAULT_NOTIFICATION_CHANNEL", "fake")
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    repository.init_schema()
    return repository


def test_incident_notification_handler_reuses_scheduler_and_outbox(
    runtime_repository,
):
    incident = create_s4_incident(runtime_repository)
    review = IncidentReviewService(runtime_repository).create_initial_review(
        incident.incident_id,
        required_by=NOW + timedelta(hours=2),
        created_at=NOW + timedelta(minutes=1),
    )
    sent_at = NOW + timedelta(minutes=2)
    mark_notification(
        runtime_repository,
        review.notification.notification_id,
        now=sent_at,
    )
    repository = AutomationRepository(runtime_repository)
    job = ensure_incident_notification_automation_job(
        repository,
        platform_name="synthetic-platform",
        now=sent_at,
    )
    service = AutomationService(
        repository,
        handlers=build_incident_notification_handlers(
            runtime_repository=runtime_repository,
        ),
        clock=lambda: sent_at + timedelta(minutes=5),
        owner_token="incident-maintenance-test",
    )

    first = service.run_cycle()

    assert job.job_id == INCIDENT_NOTIFICATION_JOB_ID
    assert job.job_type == INCIDENT_NOTIFICATION_MAINTENANCE
    assert first.errors == ()
    assert len(first.completed_run_ids) == 1
    run = repository.list_runs(job_id=job.job_id)[0]
    assert run.run_status is AutomationRunStatus.SUCCESS, (
        run.error_code,
        run.error_message,
    )
    assert run.output_manifest_sha256.startswith("sha256:")
    finished = next(
        event
        for event in repository.list_events(run.run_id)
        if event.event_type == "RUN_FINISHED"
    )
    assert finished.payload["platform_write_performed"] is False
    assert finished.payload["worker_recovery_performed"] is False
    assert finished.payload["midpoint_queued_count"] == 1
    assert finished.payload["pulse_eligible_count"] == 0
    assert (
        len(
            runtime_repository.list_notification_outbox(
                related_review_task_id=review.review_task.review_task_id
            )
        )
        == 2
    )

    second_service = AutomationService(
        repository,
        handlers=build_incident_notification_handlers(
            runtime_repository=runtime_repository,
        ),
        clock=lambda: sent_at + timedelta(minutes=6),
        owner_token="incident-maintenance-test-2",
    )
    second = second_service.run_cycle()

    assert second.errors == ()
    assert len(second.completed_run_ids) == 1
    assert (
        len(
            runtime_repository.list_notification_outbox(
                related_review_task_id=review.review_task.review_task_id
            )
        )
        == 2
    )


def test_incident_automation_reports_disabled_worker_recovery_without_host_action(
    runtime_repository,
    tmp_path,
):
    incident = create_s4_incident(runtime_repository)
    repository = AutomationRepository(runtime_repository)
    job = ensure_incident_notification_automation_job(
        repository,
        platform_name="synthetic-platform",
        now=NOW,
    )
    recovery = ShadowBotWorkerRecoveryCoordinator(
        runtime_repository,
        queue_dir=tmp_path / "queue",
        enabled=False,
    )
    service = AutomationService(
        repository,
        handlers=build_incident_notification_handlers(
            runtime_repository=runtime_repository,
            worker_recovery=recovery,
        ),
        clock=lambda: NOW,
        owner_token="incident-recovery-disabled-test",
    )

    cycle = service.run_cycle()

    assert cycle.errors == ()
    run = repository.list_runs(job_id=job.job_id)[0]
    assert run.run_status is AutomationRunStatus.SUCCESS
    finished = next(
        event
        for event in repository.list_events(run.run_id)
        if event.event_type == "RUN_FINISHED"
    )
    assert finished.payload["worker_recovery_count"] == 1
    assert finished.payload["worker_recovery_performed"] is False
    assert finished.payload["worker_recovery_results"] == [
        {
            "action": "",
            "host_action_performed": False,
            "incident_id": incident.incident_id,
            "status": "HOST_RECOVERY_DISABLED",
            "watchdog_event_count": 0,
            "write_unknown_risk": False,
        }
    ]

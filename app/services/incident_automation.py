from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from app.automation_models import AutomationJob, AutomationRun, AutomationRunOutcome
from app.enums import AutomationRunStatus, IncidentCategory, ReviewTaskStatus
from app.repositories.automation_repository import AutomationRepository
from app.repositories.operational_incident_repository import (
    OperationalIncidentRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import (
    INTERVAL_MINUTES,
    AutomationExecutionContext,
    AutomationHandler,
)
from app.services.incident_management import IncidentNotificationService
from app.services.shadowbot_worker_recovery import (
    ShadowBotWorkerRecoveryCoordinator,
)

INCIDENT_NOTIFICATION_MAINTENANCE = "INCIDENT_NOTIFICATION_MAINTENANCE"
INCIDENT_NOTIFICATION_JOB_ID = "AUTOMATION-INCIDENT-NOTIFICATION-MAINTENANCE"


@dataclass(frozen=True, slots=True)
class IncidentNotificationAutomationHandler:
    runtime_repository: SQLiteRuntimeRepository
    worker_recovery: ShadowBotWorkerRecoveryCoordinator | None = None

    def __call__(
        self,
        run: AutomationRun,
        context: AutomationExecutionContext,
    ) -> AutomationRunOutcome:
        if run.job_type != INCIDENT_NOTIFICATION_MAINTENANCE:
            raise ValueError(
                "IncidentNotificationAutomationHandler only accepts Incident maintenance"
            )
        if not context.heartbeat():
            raise RuntimeError("Automation lease was lost before Incident maintenance")

        service = IncidentNotificationService(self.runtime_repository)
        results: list[dict[str, object]] = []
        pending_reviews = self.runtime_repository.list_review_tasks(
            status=ReviewTaskStatus.PENDING
        )
        for review in pending_reviews:
            if (
                review.review_type != "emergency_protection"
                or review.scope_type != "incident"
                or review.platform_name != run.platform_name
            ):
                continue
            if not context.heartbeat():
                raise RuntimeError(
                    "Automation lease was lost during Incident maintenance"
                )
            incident_id = review.scope_key
            timing = service.sync_initial_delivery(
                incident_id,
                review.review_task_id,
            )
            reminder = service.enqueue_midpoint_if_due(
                incident_id,
                review.review_task_id,
                now=context.clock(),
            )
            if reminder.notification is not None:
                timing = service.sync_midpoint_delivery(
                    incident_id,
                    review.review_task_id,
                    reminder.notification.notification_id,
                )
            pulse = service.evaluate_online_pulse_eligibility(
                incident_id,
                review.review_task_id,
            )
            results.append(
                {
                    "incident_id": incident_id,
                    "review_task_id": review.review_task_id,
                    "escalation_state": timing.escalation_state,
                    "notification_count": timing.notification_count,
                    "reminder_status": reminder.status,
                    "pulse_eligible": pulse.eligible,
                    "pulse_reason": pulse.reason,
                    "pulse_run_id": pulse.pulse_run_id or "",
                }
            )

        recovery_results: list[dict[str, object]] = []
        if self.worker_recovery is not None:
            incident_repository = OperationalIncidentRepository(self.runtime_repository)
            for incident in incident_repository.list_active(
                category=IncidentCategory.WORKER_UNAVAILABLE
            ):
                if incident.platform_name not in {None, run.platform_name}:
                    continue
                if not context.heartbeat():
                    raise RuntimeError(
                        "Automation lease was lost during Worker recovery"
                    )
                recovered = self.worker_recovery.recover(
                    incident.incident_id,
                    now=context.clock(),
                )
                recovery_results.append(
                    {
                        "incident_id": incident.incident_id,
                        "status": recovered.status,
                        "action": recovered.action,
                        "host_action_performed": (recovered.host_action_performed),
                        "watchdog_event_count": (recovered.watchdog_event_count),
                        "write_unknown_risk": recovered.write_unknown_risk,
                    }
                )

        manifest = _manifest_sha256(
            {
                "review_results": results,
                "worker_recovery_results": recovery_results,
            }
        )
        return AutomationRunOutcome(
            status=AutomationRunStatus.SUCCESS,
            output_manifest_sha256=manifest,
            event_payload={
                "schema_version": "incident-notification-maintenance-result-v1",
                "review_count": len(results),
                "midpoint_queued_count": sum(
                    item["reminder_status"] == "MIDPOINT_QUEUED" for item in results
                ),
                "pulse_eligible_count": sum(
                    bool(item["pulse_eligible"]) for item in results
                ),
                "platform_write_performed": False,
                "worker_recovery_performed": any(
                    bool(item["host_action_performed"]) for item in recovery_results
                ),
                "worker_recovery_count": len(recovery_results),
                "worker_recovery_results": recovery_results,
                "results": results,
            },
        )


def incident_notification_automation_job(
    *,
    platform_name: str,
) -> AutomationJob:
    platform = platform_name.strip()
    if not platform:
        raise ValueError("platform_name must not be blank")
    return AutomationJob(
        job_id=INCIDENT_NOTIFICATION_JOB_ID,
        job_type=INCIDENT_NOTIFICATION_MAINTENANCE,
        display_name="异常通知与复核状态维护",
        enabled=True,
        schedule_kind=INTERVAL_MINUTES,
        schedule_expression="1",
        priority=20,
        config={
            "platform_name": platform,
            "catchup_policy": "LATEST_ONLY",
            "max_lateness_seconds": 120,
            "requires_ui_channel": False,
            "platform_write_performed": False,
        },
    )


def ensure_incident_notification_automation_job(
    repository: AutomationRepository,
    *,
    platform_name: str,
    now: datetime,
) -> AutomationJob:
    job = incident_notification_automation_job(platform_name=platform_name)
    existing = repository.get_job(job.job_id)
    if existing is not None:
        repository.validate_job_static_identity(job)
        return existing
    return repository.upsert_job(job, now=now)


def build_incident_notification_handlers(
    *,
    runtime_repository: SQLiteRuntimeRepository,
    worker_recovery: ShadowBotWorkerRecoveryCoordinator | None = None,
) -> Mapping[str, AutomationHandler]:
    return {
        INCIDENT_NOTIFICATION_MAINTENANCE: (
            IncidentNotificationAutomationHandler(
                runtime_repository,
                worker_recovery=worker_recovery,
            )
        )
    }


def _manifest_sha256(results: object) -> str:
    encoded = json.dumps(
        results,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()

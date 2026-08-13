from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol

from app.automation_models import AutomationJob, AutomationRun, AutomationRunOutcome
from app.enums import AutomationRunStatus
from app.repositories.automation_repository import AutomationRepository
from app.services.automation import (
    MANUAL_ONLY,
    AutomationExecutionContext,
    AutomationHandler,
)


RELEASE_BACKUP_MAINTENANCE = "RELEASE_BACKUP_MAINTENANCE"
RELEASE_BACKUP_JOB_ID = "AUTOMATION-RELEASE-BACKUP-MAINTENANCE"


class ReleaseBackupExecutor(Protocol):
    def __call__(self, backup_id: str) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class ReleaseBackupAutomationHandler:
    """Run one fixed backup operation outside the Web request lifecycle."""

    executor: ReleaseBackupExecutor

    def __call__(
        self,
        run: AutomationRun,
        context: AutomationExecutionContext,
    ) -> AutomationRunOutcome:
        if run.job_type != RELEASE_BACKUP_MAINTENANCE:
            raise ValueError(
                "ReleaseBackupAutomationHandler only accepts backup maintenance"
            )
        if not context.heartbeat():
            raise RuntimeError("Automation lease was lost before backup maintenance")
        result = dict(self.executor(run.run_id))
        if not context.heartbeat():
            raise RuntimeError("Automation lease was lost after backup maintenance")
        backup_id = str(result.get("backup_id") or "").strip()
        if backup_id != run.run_id or result.get("verified") is not True:
            raise RuntimeError("Backup executor did not return a verified run-bound backup")
        safe_result = {
            "backup_id": backup_id,
            "verified": True,
            "created": bool(result.get("created")),
        }
        manifest = _manifest_sha256(safe_result)
        return AutomationRunOutcome(
            status=AutomationRunStatus.SUCCESS,
            output_manifest_sha256=manifest,
            event_payload={
                "schema_version": "release-backup-maintenance-result-v1",
                **safe_result,
                "platform_write_performed": False,
            },
        )


def release_backup_automation_job(*, platform_name: str) -> AutomationJob:
    platform = platform_name.strip()
    if not platform:
        raise ValueError("platform_name must not be blank")
    return AutomationJob(
        job_id=RELEASE_BACKUP_JOB_ID,
        job_type=RELEASE_BACKUP_MAINTENANCE,
        display_name="受控运行备份",
        enabled=True,
        schedule_kind=MANUAL_ONLY,
        schedule_expression="manual",
        priority=90,
        config={
            "platform_name": platform,
            "catchup_policy": "NONE",
            "requires_ui_channel": False,
            "platform_write_performed": False,
        },
    )


def ensure_release_backup_automation_job(
    repository: AutomationRepository,
    *,
    platform_name: str,
    now: datetime,
) -> AutomationJob:
    job = release_backup_automation_job(platform_name=platform_name)
    existing = repository.get_job(job.job_id)
    if existing is not None:
        repository.validate_job_static_identity(job)
        return existing
    return repository.upsert_job(job, now=now)


def build_maintenance_handlers(
    *,
    release_backup_executor: ReleaseBackupExecutor,
) -> Mapping[str, AutomationHandler]:
    return {
        RELEASE_BACKUP_MAINTENANCE: ReleaseBackupAutomationHandler(
            release_backup_executor
        )
    }


def _manifest_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()

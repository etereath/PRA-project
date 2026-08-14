"""Explicit, atomic maintenance for operational cutoffs and timed Jobs."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from typing import Callable

from app.automation_models import AutomationJob
from app.repositories.automation_repository import AutomationRepository
from app.services.automation import (
    DAILY_LOCAL_TIME,
    DAILY_TASK_GENERATION,
    PLATFORM_TRADE_DAY_SETTLEMENT,
    POST_CUTOFF_PULSE,
    PRE_CUTOFF_FULL_SCAN,
    SALES_PLAN_INPUT_BUILD,
)
from app.services.automation_job_versioning import (
    automation_configuration_version,
    versioned_automation_job_id,
)
from app.services.operational_time import (
    DEFAULT_OPERATIONAL_TIMEZONE,
    OperationalTimePolicy,
    OperationalTimePolicyRegistry,
)


POLICY_BOUND_JOB_TYPES = (
    PRE_CUTOFF_FULL_SCAN,
    POST_CUTOFF_PULSE,
    PLATFORM_TRADE_DAY_SETTLEMENT,
    SALES_PLAN_INPUT_BUILD,
    DAILY_TASK_GENERATION,
)


class OperationalTimeMaintenanceError(ValueError):
    """The coordinated maintenance request is unsafe or stale."""


@dataclass(frozen=True, slots=True)
class OperationalTimeMaintenanceResult:
    previous_policy_version: str
    successor_policy: OperationalTimePolicy
    successor_jobs: tuple[AutomationJob, ...]


class OperationalTimeMaintenanceService:
    """Switch one policy and every dependent schedule in one transaction."""

    def __init__(
        self,
        automation: AutomationRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.automation = automation
        self.clock = clock

    def replace_policy_and_timed_jobs(
        self,
        *,
        platform_name: str,
        expected_current_policy_version: str,
        successor_policy_version: str,
        platform_cutoff_local_time: time,
        seller_cutoff_local_time: time,
        peak_start_local_time: time,
        created_by: str,
    ) -> OperationalTimeMaintenanceResult:
        platform = str(platform_name).strip()
        expected_version = str(expected_current_policy_version).strip()
        successor_version = str(successor_policy_version).strip()
        actor = str(created_by).strip()
        if not platform:
            raise OperationalTimeMaintenanceError(
                "platform_name must not be blank"
            )
        if not expected_version or not successor_version:
            raise OperationalTimeMaintenanceError(
                "operational time policy versions must not be blank"
            )
        if not actor:
            raise OperationalTimeMaintenanceError("created_by must not be blank")

        now = self._now()
        policies = self.automation.load_operational_time_policies()
        effective_policy = OperationalTimePolicyRegistry(policies).select(now)
        open_policies = tuple(
            policy for policy in policies if policy.effective_to is None
        )
        if (
            len(open_policies) != 1
            or open_policies[0].policy_version != expected_version
            or effective_policy.policy_version != expected_version
        ):
            raise OperationalTimeMaintenanceError(
                "current operational time policy changed; refresh the maintenance "
                "request"
            )

        successor_policy = OperationalTimePolicy(
            policy_version=successor_version,
            timezone_name=DEFAULT_OPERATIONAL_TIMEZONE,
            platform_cutoff_local_time=platform_cutoff_local_time,
            seller_cutoff_local_time=seller_cutoff_local_time,
            peak_start_local_time=peak_start_local_time,
            effective_from=now,
        )
        current_jobs = {
            job_type: self._current_job_version(
                platform_name=platform,
                job_type=job_type,
                expected_policy_version=expected_version,
            )
            for job_type in POLICY_BOUND_JOB_TYPES
        }
        plan_current = current_jobs[SALES_PLAN_INPUT_BUILD]
        plan_offset = self._bounded_offset(
            plan_current.config.get("settlement_offset_minutes"),
            minimum=5,
            maximum=30,
            label="settlement_offset_minutes",
        )
        plan_successor = self._successor(
            current=plan_current,
            successor_policy=successor_policy,
            schedule_expression=self._time_expression(
                successor_policy.seller_cutoff_local_time,
                plan_offset,
            ),
            display_name=plan_current.display_name,
            now=now,
            updated_by=actor,
            config_updates={"settlement_offset_minutes": plan_offset},
        )

        daily_current = current_jobs[DAILY_TASK_GENERATION]
        daily_offset = self._bounded_offset(
            daily_current.config.get("plan_input_offset_minutes"),
            minimum=0,
            maximum=30,
            label="plan_input_offset_minutes",
        )
        daily_successor = self._successor(
            current=daily_current,
            successor_policy=successor_policy,
            schedule_expression=self._time_expression(
                successor_policy.seller_cutoff_local_time,
                plan_offset + daily_offset,
            ),
            display_name=daily_current.display_name,
            now=now,
            updated_by=actor,
            config_updates={
                "plan_input_offset_minutes": daily_offset,
                "sales_plan_input_offset_minutes": plan_offset,
                "upstream_configuration_version": plan_successor.config[
                    "configuration_version"
                ],
            },
        )
        cutoff_label = successor_policy.platform_cutoff_local_time.strftime(
            "%H:%M"
        )
        seller_label = successor_policy.seller_cutoff_local_time.strftime(
            "%H:%M"
        )
        successors = {
            PRE_CUTOFF_FULL_SCAN: self._successor(
                current=current_jobs[PRE_CUTOFF_FULL_SCAN],
                successor_policy=successor_policy,
                schedule_expression=self._time_expression(
                    successor_policy.platform_cutoff_local_time,
                    -5,
                ),
                display_name=f"{cutoff_label} 截单前完整扫描",
                now=now,
                updated_by=actor,
            ),
            POST_CUTOFF_PULSE: self._successor(
                current=current_jobs[POST_CUTOFF_PULSE],
                successor_policy=successor_policy,
                schedule_expression=self._time_expression(
                    successor_policy.platform_cutoff_local_time,
                    5,
                ),
                display_name=f"{cutoff_label} 截单后确认扫描",
                now=now,
                updated_by=actor,
            ),
            PLATFORM_TRADE_DAY_SETTLEMENT: self._successor(
                current=current_jobs[PLATFORM_TRADE_DAY_SETTLEMENT],
                successor_policy=successor_policy,
                schedule_expression=self._time_expression(
                    successor_policy.seller_cutoff_local_time,
                    0,
                ),
                display_name=f"{seller_label} 平台交易日结算",
                now=now,
                updated_by=actor,
            ),
            SALES_PLAN_INPUT_BUILD: plan_successor,
            DAILY_TASK_GENERATION: daily_successor,
        }
        replacements = tuple(
            (current_jobs[job_type].job_id, successors[job_type])
            for job_type in POLICY_BOUND_JOB_TYPES
        )
        try:
            stored = self.automation.replace_time_policy_and_job_versions(
                expected_current_policy_version=expected_version,
                successor_policy=successor_policy,
                created_by=actor,
                replacements=replacements,
                now=now,
            )
        except (RuntimeError, ValueError, sqlite3.DatabaseError) as exc:
            raise OperationalTimeMaintenanceError(str(exc)) from exc
        return OperationalTimeMaintenanceResult(
            previous_policy_version=expected_version,
            successor_policy=successor_policy,
            successor_jobs=stored,
        )

    def _current_job_version(
        self,
        *,
        platform_name: str,
        job_type: str,
        expected_policy_version: str,
    ) -> AutomationJob:
        candidates = tuple(
            job
            for job in self.automation.list_jobs()
            if job.job_type == job_type
            and str(job.config.get("platform_name") or "") == platform_name
        )
        enabled = tuple(job for job in candidates if job.enabled)
        if len(enabled) > 1:
            raise OperationalTimeMaintenanceError(
                f"{job_type} has multiple enabled versions"
            )
        if enabled:
            current = enabled[0]
        else:
            versioned = tuple(
                (job, self._configuration_effective_at(job))
                for job in candidates
                if str(job.config.get("effective_at") or "").strip()
            )
            latest_at = max(
                (effective_at for _, effective_at in versioned),
                default=None,
            )
            latest = tuple(
                job
                for job, effective_at in versioned
                if effective_at == latest_at
            )
            if not latest:
                latest_at = max(
                    (
                        job.updated_at or job.created_at
                        for job in candidates
                        if job.updated_at is not None
                        or job.created_at is not None
                    ),
                    default=None,
                )
                latest = tuple(
                    job
                    for job in candidates
                    if (job.updated_at or job.created_at) == latest_at
                )
            if latest_at is None or len(latest) != 1:
                raise OperationalTimeMaintenanceError(
                    f"{job_type} current version is not unique"
                )
            current = latest[0]
        if current.schedule_kind != DAILY_LOCAL_TIME:
            raise OperationalTimeMaintenanceError(
                f"{job_type} is not a policy-bound daily schedule"
            )
        if (
            str(current.config.get("time_policy_version") or "")
            != expected_policy_version
        ):
            raise OperationalTimeMaintenanceError(
                f"{job_type} is not aligned with the current time policy"
            )
        return current

    @staticmethod
    def _configuration_effective_at(job: AutomationJob) -> datetime:
        try:
            value = datetime.fromisoformat(str(job.config["effective_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise OperationalTimeMaintenanceError(
                f"{job.job_type} configuration effective_at is invalid"
            ) from exc
        if value.tzinfo is None or value.utcoffset() is None:
            raise OperationalTimeMaintenanceError(
                f"{job.job_type} configuration effective_at is not timezone-aware"
            )
        return value.astimezone(timezone.utc)

    @staticmethod
    def _successor(
        *,
        current: AutomationJob,
        successor_policy: OperationalTimePolicy,
        schedule_expression: str,
        display_name: str,
        now: datetime,
        updated_by: str,
        config_updates: dict[str, object] | None = None,
    ) -> AutomationJob:
        config = dict(current.config)
        for key in ("updated_by", "effective_at", "configuration_version"):
            config.pop(key, None)
        config["time_policy_version"] = successor_policy.policy_version
        config.update(config_updates or {})
        version = automation_configuration_version(
            job_type=current.job_type,
            schedule_kind=DAILY_LOCAL_TIME,
            schedule_expression=schedule_expression,
            config=config,
        )
        config.update(
            {
                "configuration_schema": "pra-automation-config-v1",
                "configuration_version": version,
                "updated_by": updated_by,
                "effective_at": now.isoformat(),
            }
        )
        return replace(
            current,
            job_id=versioned_automation_job_id(current.job_type, version),
            display_name=display_name,
            schedule_kind=DAILY_LOCAL_TIME,
            schedule_expression=schedule_expression,
            config=config,
            created_at=None,
            updated_at=None,
        )

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise OperationalTimeMaintenanceError(
                "operational time maintenance clock must be timezone-aware"
            )
        return value.astimezone(timezone.utc)

    @staticmethod
    def _bounded_offset(
        value: object,
        *,
        minimum: int,
        maximum: int,
        label: str,
    ) -> int:
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise OperationalTimeMaintenanceError(f"{label} is invalid") from exc
        if not minimum <= normalized <= maximum:
            raise OperationalTimeMaintenanceError(f"{label} is outside its range")
        return normalized

    @staticmethod
    def _time_expression(base: time, offset_minutes: int) -> str:
        anchor = datetime(2000, 1, 1, base.hour, base.minute)
        return (anchor + timedelta(minutes=offset_minutes)).strftime("%H:%M")

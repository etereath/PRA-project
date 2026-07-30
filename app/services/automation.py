from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.automation_ui_channel import UI_AUTOMATION_JOB_TYPES
from app.automation_models import (
    AutomationCycleResult,
    AutomationJob,
    AutomationRun,
    AutomationRunClaim,
    AutomationRunOutcome,
    AutomationScheduleResult,
)
from app.enums import AutomationRunStatus
from app.repositories.automation_repository import AutomationRepository
from app.services.operational_time import (
    DEFAULT_OPERATIONAL_TIMEZONE,
    OperationalTimeService,
)


INTERVAL_MINUTES = "INTERVAL_MINUTES"
DAILY_LOCAL_TIME = "DAILY_LOCAL_TIME"
CHILD_ONLY = "CHILD_ONLY"

ONLINE_PULSE = "ONLINE_PULSE"
FULL_MARKET_SCAN = "FULL_MARKET_SCAN"
LISTING_STATUS_SCAN = "LISTING_STATUS_SCAN"
ORDER_SCAN = "ORDER_SCAN"
PRE_CUTOFF_FULL_SCAN = "PRE_CUTOFF_FULL_SCAN"
POST_CUTOFF_PULSE = "POST_CUTOFF_PULSE"
PLATFORM_TRADE_DAY_SETTLEMENT = "PLATFORM_TRADE_DAY_SETTLEMENT"
SALES_PLAN_INPUT_BUILD = "SALES_PLAN_INPUT_BUILD"

UI_JOB_TYPES = UI_AUTOMATION_JOB_TYPES

# Lower values have higher priority.  The first three classes are enforced by
# the existing operation/write-lock ledger before any scan is dispatched.
UI_CHANNEL_PRIORITY = {
    "UNKNOWN_OR_RECONCILE": 0,
    "SYSTEM_EMERGENCY_SET_OFFLINE": 10,
    "AUTHORIZED_WRITE": 20,
    PRE_CUTOFF_FULL_SCAN: 30,
    POST_CUTOFF_PULSE: 35,
    PLATFORM_TRADE_DAY_SETTLEMENT: 40,
    FULL_MARKET_SCAN: 50,
    ONLINE_PULSE: 60,
}

DEFAULT_MAX_WINDOWS_PER_JOB = 16
DEFAULT_MAX_RUNS_PER_CYCLE = 8


class AutomationHandler(Protocol):
    def __call__(
        self,
        run: AutomationRun,
        context: "AutomationExecutionContext",
    ) -> AutomationRunOutcome: ...


class AutomationExecutionContext:
    """Fenced helper surface exposed to one automation handler."""

    def __init__(
        self,
        *,
        claim: AutomationRunClaim,
        repository: AutomationRepository,
        operational_time: OperationalTimeService,
        clock: Callable[[], datetime],
        lease_seconds: int,
    ) -> None:
        self.claim = claim
        self.repository = repository
        self.operational_time = operational_time
        self.clock = clock
        self.lease_seconds = lease_seconds

    def heartbeat(self) -> bool:
        renewed = self.repository.renew_lease(
            self.claim,
            clock=self.clock,
            lease_seconds=self.lease_seconds,
        )
        if renewed is None:
            return False
        self.claim = renewed
        return True

    def bind_input_manifest(self, manifest_sha256: str) -> AutomationRun:
        return self.repository.bind_run_input_manifest(
            self.claim,
            manifest_sha256=manifest_sha256,
            clock=self.clock,
        )

    def ensure_child_run(
        self,
        *,
        child_job_id: str,
        relation_type: str,
    ) -> tuple[AutomationRun, bool]:
        child_job = self.repository.get_job(child_job_id)
        if child_job is None:
            raise ValueError("Child automation job does not exist")
        return self.repository.ensure_child_run_fenced(
            self.claim,
            child_job,
            relation_type=relation_type,
            clock=self.clock,
        )


class AutomationSchedulePlanner:
    """Materialize bounded, idempotent schedule windows into the v14 ledger."""

    def __init__(
        self,
        repository: AutomationRepository,
        *,
        operational_time: OperationalTimeService | None = None,
        max_windows_per_job: int = DEFAULT_MAX_WINDOWS_PER_JOB,
    ) -> None:
        if max_windows_per_job < 1:
            raise ValueError("max_windows_per_job must be positive")
        self.repository = repository
        self.operational_time = operational_time or OperationalTimeService()
        self.max_windows_per_job = max_windows_per_job

    def materialize(
        self,
        *,
        now: datetime,
        executable_job_types: Iterable[str] | None = None,
    ) -> AutomationScheduleResult:
        current = _as_utc(now, "now")
        executable = frozenset(executable_job_types or ())
        self.repository.reconcile_coverage_candidates(
            executable_job_types=executable,
            now=current,
        )
        created_ids: list[str] = []
        missed_ids: list[str] = []
        merged_ids: list[str] = []
        truncated_total = 0

        for job in self.repository.list_jobs(enabled_only=True):
            if job.schedule_kind == CHILD_ONLY:
                continue
            last_scheduled_for = self.repository.latest_scheduled_for(
                job.job_id
            )
            windows, truncated = _due_windows(
                job,
                last_scheduled_for=last_scheduled_for,
                now=current,
                max_windows=self.max_windows_per_job,
            )
            truncated_total += truncated
            if not windows:
                missed_ids.extend(
                    self.repository.reconcile_scheduled_runs_for_job(
                        job,
                        now=current,
                    )
                )
                continue
            statuses = _window_statuses(job, len(windows))
            max_lateness = job.config.get("max_lateness_seconds")
            if max_lateness is not None:
                max_lateness_seconds = int(max_lateness)
                if max_lateness_seconds < 0:
                    raise ValueError(
                        "max_lateness_seconds must not be negative"
                    )
                statuses = tuple(
                    (
                        AutomationRunStatus.MISSED
                        if status is AutomationRunStatus.SCHEDULED
                        and (
                            current - scheduled_for
                        ).total_seconds()
                        > max_lateness_seconds
                        else status
                    )
                    for scheduled_for, status in zip(windows, statuses)
                )
            first_run: AutomationRun | None = None
            for scheduled_for, initial_status in zip(windows, statuses):
                time_context = self.operational_time.classify(scheduled_for)
                lateness_seconds = max(
                    int((current - scheduled_for).total_seconds()),
                    0,
                )
                run, created = self.repository.ensure_run(
                    job=job,
                    scheduled_for=scheduled_for,
                    time_context=time_context,
                    initial_status=initial_status,
                    now=current,
                    event_type=(
                        "RUN_MISSED"
                        if initial_status is AutomationRunStatus.MISSED
                        else "RUN_SCHEDULED"
                    ),
                    event_payload={
                        "scheduled_for": scheduled_for.isoformat(),
                        "actual_materialized_at": current.isoformat(),
                        "lateness_seconds": lateness_seconds,
                        "catchup_policy": str(
                            job.config.get("catchup_policy") or "LATEST_ONLY"
                        ),
                        "error_code": (
                            "MISSED_SCHEDULE_WINDOW"
                            if initial_status is AutomationRunStatus.MISSED
                            else ""
                        ),
                        "error_message": (
                            "计划窗口已错过，由后续正常窗口恢复。"
                            if initial_status is AutomationRunStatus.MISSED
                            else ""
                        ),
                    },
                )
                first_run = first_run or run
                if created:
                    created_ids.append(run.run_id)
                    if initial_status is AutomationRunStatus.MISSED:
                        missed_ids.append(run.run_id)
            if truncated and first_run is not None:
                self.repository.append_event(
                    run_id=first_run.run_id,
                    event_type="MISSED_WINDOWS_TRUNCATED",
                    now=current,
                    payload={
                        "truncated_window_count": truncated,
                        "max_windows_per_job": self.max_windows_per_job,
                    },
                )
            missed_ids.extend(
                self.repository.reconcile_scheduled_runs_for_job(
                    job,
                    now=current,
                )
            )

        by_platform: dict[str, list[AutomationRun]] = {}
        for run in self.repository.list_runs(
            statuses=(AutomationRunStatus.SCHEDULED,)
        ):
            if run.job_type in {
                ONLINE_PULSE,
                FULL_MARKET_SCAN,
                PRE_CUTOFF_FULL_SCAN,
            }:
                by_platform.setdefault(run.platform_name, []).append(run)
        for platform_runs in by_platform.values():
            full_runs = [
                run
                for run in platform_runs
                if run.job_type
                in {FULL_MARKET_SCAN, PRE_CUTOFF_FULL_SCAN}
            ]
            pulse_runs = [
                run
                for run in platform_runs
                if run.job_type == ONLINE_PULSE
            ]
            for pulse in pulse_runs:
                candidates = [
                    full
                    for full in full_runs
                    if (
                        full.platform_trade_date
                        == pulse.platform_trade_date
                    )
                    if abs(
                        (full.scheduled_for - pulse.scheduled_for)
                        .total_seconds()
                    )
                    <= 300
                ]
                if not candidates:
                    continue
                ordered_candidates = sorted(
                    candidates,
                    key=lambda run: (
                        abs(
                            (run.scheduled_for - pulse.scheduled_for)
                            .total_seconds()
                        ),
                        run.scheduled_for,
                    ),
                )
                for target in ordered_candidates:
                    if self.repository.ensure_coverage_candidate(
                        pulse_run_id=pulse.run_id,
                        target_run_id=target.run_id,
                        executable_target_job_types=executable,
                        now=current,
                    ):
                        break

        return AutomationScheduleResult(
            created_run_ids=tuple(created_ids),
            missed_run_ids=tuple(missed_ids),
            merged_run_ids=tuple(merged_ids),
            truncated_window_count=truncated_total,
        )


class AutomationService:
    """Independent scheduler/dispatcher that never performs platform writes."""

    def __init__(
        self,
        repository: AutomationRepository,
        *,
        handlers: Mapping[str, AutomationHandler] | None = None,
        operational_time: OperationalTimeService | None = None,
        clock: Callable[[], datetime] | None = None,
        owner_token: str | None = None,
        lease_seconds: int = 60,
        max_runs_per_cycle: int = DEFAULT_MAX_RUNS_PER_CYCLE,
        max_windows_per_job: int = DEFAULT_MAX_WINDOWS_PER_JOB,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if max_runs_per_cycle < 1:
            raise ValueError("max_runs_per_cycle must be positive")
        self.repository = repository
        self.handlers = dict(handlers or {})
        self._reload_operational_time = operational_time is None
        self.operational_time = (
            operational_time
            or OperationalTimeService(
                policies=repository.load_operational_time_policies()
            )
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.owner_token = owner_token or f"automation-{uuid4().hex}"
        self.lease_seconds = lease_seconds
        self.max_runs_per_cycle = max_runs_per_cycle
        self.planner = AutomationSchedulePlanner(
            repository,
            operational_time=self.operational_time,
            max_windows_per_job=max_windows_per_job,
        )

    def run_cycle(self) -> AutomationCycleResult:
        if self._reload_operational_time:
            self.operational_time = OperationalTimeService(
                policies=self.repository.load_operational_time_policies()
            )
            self.planner.operational_time = self.operational_time
        scheduled = self.planner.materialize(
            now=self.clock(),
            executable_job_types=self.handlers,
        )
        allowed_job_types = set(self.handlers)

        claimed_ids: list[str] = []
        completed_ids: list[str] = []
        merged_ids = list(scheduled.merged_run_ids)
        errors: list[str] = []
        blocker = ""
        for _ in range(self.max_runs_per_cycle):
            claim, observed_blocker = (
                self.repository.claim_next_with_ui_gate(
                    owner_token=self.owner_token,
                    clock=self.clock,
                    lease_seconds=self.lease_seconds,
                    allowed_job_types=allowed_job_types,
                    ui_job_types=UI_JOB_TYPES,
                )
            )
            blocker = observed_blocker or blocker
            if claim is None:
                break
            claimed_ids.append(claim.run.run_id)
            handler = self.handlers[claim.run.job_type]
            context = AutomationExecutionContext(
                claim=claim,
                repository=self.repository,
                operational_time=self.operational_time,
                clock=self.clock,
                lease_seconds=self.lease_seconds,
            )
            try:
                outcome = handler(claim.run, context)
                if not isinstance(outcome, AutomationRunOutcome):
                    raise TypeError(
                        "Automation handler must return "
                        "AutomationRunOutcome"
                    )
            except TimeoutError as exc:
                outcome = AutomationRunOutcome(
                    status=AutomationRunStatus.FAILED,
                    error_code="AUTOMATION_HANDLER_TIMEOUT",
                    error_message=safe_automation_error_message(exc),
                )
            except Exception as exc:
                outcome = AutomationRunOutcome(
                    status=AutomationRunStatus.FAILED,
                    error_code="AUTOMATION_HANDLER_FAILED",
                    error_message=safe_automation_error_message(exc),
                )
            completed = self.repository.finish_run(
                context.claim,
                outcome,
                clock=self.clock,
            )
            if completed:
                completed_ids.append(claim.run.run_id)
                merged_ids.extend(
                    link.child_run_id
                    for link in self.repository.list_links(
                        parent_run_id=claim.run.run_id
                    )
                    if link.relation_type == "MERGED_RUN"
                    and link.child_run_id not in merged_ids
                )
            else:
                errors.append(
                    f"LEASE_LOST:{claim.run.run_id}"
                )
                break

        return AutomationCycleResult(
            scheduled=AutomationScheduleResult(
                created_run_ids=scheduled.created_run_ids,
                missed_run_ids=scheduled.missed_run_ids,
                merged_run_ids=tuple(merged_ids),
                truncated_window_count=scheduled.truncated_window_count,
            ),
            claimed_run_ids=tuple(claimed_ids),
            completed_run_ids=tuple(completed_ids),
            blocked_reason=blocker,
            errors=tuple(errors),
        )


class AutomationHeartbeatStore:
    """UTF-8 atomic process heartbeat kept outside business tables."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def write(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{uuid4().hex}.tmp"
        )
        serialized = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                default=str,
            )
            + "\n"
        )
        try:
            with temporary.open(
                "w",
                encoding="utf-8",
                newline="\n",
            ) as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def read(self) -> dict[str, object]:
        parsed = json.loads(
            self.path.read_text(encoding="utf-8-sig")
        )
        if not isinstance(parsed, dict):
            raise ValueError("Automation heartbeat must be a JSON object")
        return parsed


def ensure_default_automation_jobs(
    repository: AutomationRepository,
    *,
    platform_name: str,
    now: datetime,
) -> tuple[AutomationJob, ...]:
    """Insert the frozen 13.5 jobs without overwriting operator changes."""

    current = _as_utc(now, "now")
    jobs = default_automation_jobs(platform_name=platform_name)
    stored: list[AutomationJob] = []
    for job in jobs:
        existing = repository.get_job(job.job_id)
        if existing is not None:
            repository.validate_job_static_identity(job)
            stored.append(existing)
            continue
        stored.append(repository.upsert_job(job, now=current))
    return tuple(stored)


def default_automation_jobs(
    *,
    platform_name: str,
) -> tuple[AutomationJob, ...]:
    platform = platform_name.strip()
    if not platform:
        raise ValueError("platform_name must not be blank")
    common = {"platform_name": platform}
    return (
        AutomationJob(
            job_id="AUTOMATION-ONLINE-PULSE-10M",
            job_type=ONLINE_PULSE,
            display_name="每 10 分钟上架中小扫描",
            enabled=True,
            schedule_kind=INTERVAL_MINUTES,
            schedule_expression="10",
            priority=UI_CHANNEL_PRIORITY[ONLINE_PULSE],
            config={
                **common,
                "catchup_policy": "LATEST_ONLY",
                "max_lateness_seconds": 600,
            },
        ),
        AutomationJob(
            job_id="AUTOMATION-FULL-MARKET-SCAN-HOURLY",
            job_type=FULL_MARKET_SCAN,
            display_name="每小时完整市场扫描",
            enabled=True,
            schedule_kind=INTERVAL_MINUTES,
            schedule_expression="60",
            priority=UI_CHANNEL_PRIORITY[FULL_MARKET_SCAN],
            config={
                **common,
                "catchup_policy": "LATEST_ONLY",
                "max_lateness_seconds": 3600,
            },
        ),
        AutomationJob(
            job_id="AUTOMATION-PRE-CUTOFF-FULL-SCAN",
            job_type=PRE_CUTOFF_FULL_SCAN,
            display_name="18:00 截单前完整扫描",
            enabled=True,
            schedule_kind=DAILY_LOCAL_TIME,
            schedule_expression="17:55",
            priority=UI_CHANNEL_PRIORITY[PRE_CUTOFF_FULL_SCAN],
            config={
                **common,
                "catchup_policy": "LATEST_ONLY",
                "max_lateness_seconds": 7200,
            },
        ),
        AutomationJob(
            job_id="AUTOMATION-POST-CUTOFF-PULSE",
            job_type=POST_CUTOFF_PULSE,
            display_name="18:00 截单后确认扫描",
            enabled=True,
            schedule_kind=DAILY_LOCAL_TIME,
            schedule_expression="18:05",
            priority=UI_CHANNEL_PRIORITY[POST_CUTOFF_PULSE],
            config={
                **common,
                "catchup_policy": "LATEST_ONLY",
                "max_lateness_seconds": 7200,
            },
        ),
        AutomationJob(
            job_id="AUTOMATION-TRADE-DAY-SETTLEMENT",
            job_type=PLATFORM_TRADE_DAY_SETTLEMENT,
            display_name="20:00 平台交易日结算",
            enabled=True,
            schedule_kind=DAILY_LOCAL_TIME,
            schedule_expression="20:00",
            priority=UI_CHANNEL_PRIORITY[
                PLATFORM_TRADE_DAY_SETTLEMENT
            ],
            config={
                **common,
                "catchup_policy": "IDEMPOTENT",
                "max_catchup_runs": 2,
                "requires_ui_channel": False,
            },
        ),
        AutomationJob(
            job_id="AUTOMATION-SALES-PLAN-INPUT",
            job_type=SALES_PLAN_INPUT_BUILD,
            display_name="下一销售计划输入构建",
            enabled=True,
            schedule_kind=DAILY_LOCAL_TIME,
            schedule_expression="20:05",
            priority=55,
            config={
                **common,
                "catchup_policy": "IDEMPOTENT",
                "max_catchup_runs": 2,
                "requires_ui_channel": False,
            },
        ),
        AutomationJob(
            job_id="AUTOMATION-LISTING-STATUS-SCAN-CHILD",
            job_type=LISTING_STATUS_SCAN,
            display_name="完整市场扫描—商品状态子运行",
            enabled=False,
            schedule_kind=CHILD_ONLY,
            schedule_expression="-",
            priority=UI_CHANNEL_PRIORITY[FULL_MARKET_SCAN],
            config={**common, "requires_ui_channel": True},
        ),
        AutomationJob(
            job_id="AUTOMATION-ORDER-SCAN-CHILD",
            job_type=ORDER_SCAN,
            display_name="完整市场扫描—订单历史子运行",
            enabled=False,
            schedule_kind=CHILD_ONLY,
            schedule_expression="-",
            priority=UI_CHANNEL_PRIORITY[FULL_MARKET_SCAN] + 1,
            config={**common, "requires_ui_channel": True},
        ),
    )


def _due_windows(
    job: AutomationJob,
    *,
    last_scheduled_for: datetime | None,
    now: datetime,
    max_windows: int,
) -> tuple[tuple[datetime, ...], int]:
    if job.schedule_kind == INTERVAL_MINUTES:
        minutes = _parse_interval_minutes(job.schedule_expression)
        latest = _floor_interval(now, minutes)
        if last_scheduled_for is None:
            return (latest,), 0
        previous = _as_utc(last_scheduled_for, "last_scheduled_for")
        step = timedelta(minutes=minutes)
        count = max(int((latest - previous) // step), 0)
        if count == 0:
            return (), 0
        kept_count = min(count, max_windows)
        first = latest - step * (kept_count - 1)
        return (
            tuple(first + step * index for index in range(kept_count)),
            count - kept_count,
        )
    if job.schedule_kind == DAILY_LOCAL_TIME:
        local_schedule = _parse_local_time(job.schedule_expression)
        latest = _latest_daily_window(now, local_schedule)
        if last_scheduled_for is None:
            return (latest,), 0
        previous = _as_utc(
            last_scheduled_for,
            "last_scheduled_for",
        ).astimezone(ZoneInfo(DEFAULT_OPERATIONAL_TIMEZONE))
        latest_local = latest.astimezone(
            ZoneInfo(DEFAULT_OPERATIONAL_TIMEZONE)
        )
        count = max((latest_local.date() - previous.date()).days, 0)
        if count == 0:
            return (), 0
        kept_count = min(count, max_windows)
        first_date = latest_local.date() - timedelta(
            days=kept_count - 1
        )
        timezone_name = ZoneInfo(DEFAULT_OPERATIONAL_TIMEZONE)
        windows = tuple(
            datetime.combine(
                first_date + timedelta(days=index),
                local_schedule,
                tzinfo=timezone_name,
            ).astimezone(timezone.utc)
            for index in range(kept_count)
        )
        return windows, count - kept_count
    raise ValueError(
        f"Unsupported automation schedule_kind: {job.schedule_kind}"
    )


def _window_statuses(
    job: AutomationJob,
    window_count: int,
) -> tuple[AutomationRunStatus, ...]:
    if window_count < 1:
        return ()
    policy = str(job.config.get("catchup_policy") or "LATEST_ONLY")
    if policy == "IDEMPOTENT":
        max_catchup = int(job.config.get("max_catchup_runs") or 1)
        if max_catchup < 1:
            raise ValueError("max_catchup_runs must be positive")
        scheduled_count = min(window_count, max_catchup)
    elif policy == "LATEST_ONLY":
        scheduled_count = 1
    else:
        raise ValueError(f"Unsupported catchup_policy: {policy}")
    missed_count = window_count - scheduled_count
    return (
        *(AutomationRunStatus.MISSED for _ in range(missed_count)),
        *(
            AutomationRunStatus.SCHEDULED
            for _ in range(scheduled_count)
        ),
    )


def _floor_interval(value: datetime, minutes: int) -> datetime:
    current = _as_utc(value, "value")
    local = current.astimezone(ZoneInfo(DEFAULT_OPERATIONAL_TIMEZONE))
    floored = local.replace(
        minute=local.minute - (local.minute % minutes),
        second=0,
        microsecond=0,
    )
    return floored.astimezone(timezone.utc)


def _latest_daily_window(value: datetime, local_time: time) -> datetime:
    current = _as_utc(value, "value")
    timezone_name = ZoneInfo(DEFAULT_OPERATIONAL_TIMEZONE)
    local = current.astimezone(timezone_name)
    scheduled_date = local.date()
    candidate = datetime.combine(
        scheduled_date,
        local_time,
        tzinfo=timezone_name,
    )
    if candidate > local:
        candidate -= timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _parse_interval_minutes(expression: str) -> int:
    try:
        minutes = int(expression)
    except ValueError as exc:
        raise ValueError(
            "INTERVAL_MINUTES schedule_expression must be an integer"
        ) from exc
    if minutes < 1 or minutes > 1440 or 60 % minutes != 0:
        raise ValueError(
            "Interval minutes must be a positive divisor of 60"
        )
    return minutes


def _parse_local_time(expression: str) -> time:
    try:
        parsed = time.fromisoformat(expression)
    except ValueError as exc:
        raise ValueError(
            "DAILY_LOCAL_TIME schedule_expression must be HH:MM"
        ) from exc
    if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise ValueError(
            "DAILY_LOCAL_TIME schedule_expression must be minute precision"
        )
    return parsed


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def safe_automation_error_message(error: BaseException) -> str:
    message = str(error).strip() or type(error).__name__
    message = re.sub(
        r"(?i)(?:[A-Z]:\\|\\\\)[^\s\"']+",
        "<local-path>",
        message,
    )
    message = re.sub(
        r"(?<![A-Za-z0-9])/(?:home|Users|var|tmp)/[^\s\"']+",
        "<local-path>",
        message,
    )
    return message[:2000]

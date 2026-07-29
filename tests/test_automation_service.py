from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.automation_models import (
    AutomationJob,
    AutomationRunOutcome,
)
from app.enums import (
    AutomationRunStatus,
    TaskActionType,
    TaskOriginType,
    TaskStatus,
)
from app.models import ShadowBotOperationLedger, Task
from app.repositories.automation_repository import AutomationRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import (
    AutomationHeartbeatStore,
    AutomationSchedulePlanner,
    AutomationService,
    CHILD_ONLY,
    DAILY_LOCAL_TIME,
    FULL_MARKET_SCAN,
    INTERVAL_MINUTES,
    LISTING_STATUS_SCAN,
    ONLINE_PULSE,
    ensure_default_automation_jobs,
)
from app.services.operational_time import OperationalTimeService
from scripts.run_automation_service import ProcessFileLock


PLATFORM = "蚂蚁花团供应商"


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def runtime_repository(tmp_path: Path) -> SQLiteRuntimeRepository:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    return repository


@pytest.fixture
def repository(
    runtime_repository: SQLiteRuntimeRepository,
) -> AutomationRepository:
    return AutomationRepository(runtime_repository)


def _job(
    *,
    job_id: str,
    job_type: str,
    minutes: int = 10,
    priority: int = 50,
    enabled: bool = True,
    schedule_kind: str = INTERVAL_MINUTES,
) -> AutomationJob:
    return AutomationJob(
        job_id=job_id,
        job_type=job_type,
        display_name=f"{job_type} 测试作业",
        enabled=enabled,
        schedule_kind=schedule_kind,
        schedule_expression=(
            str(minutes) if schedule_kind == INTERVAL_MINUTES else "-"
        ),
        priority=priority,
        config={
            "platform_name": PLATFORM,
            "catchup_policy": "LATEST_ONLY",
        },
    )


def _store_job(
    repository: AutomationRepository,
    job: AutomationJob,
    *,
    now: datetime,
) -> AutomationJob:
    return repository.upsert_job(job, now=now)


def _ensure_run(
    repository: AutomationRepository,
    job: AutomationJob,
    *,
    scheduled_for: datetime,
):
    return repository.ensure_run(
        job=job,
        scheduled_for=scheduled_for,
        time_context=OperationalTimeService().classify(scheduled_for),
        initial_status=AutomationRunStatus.SCHEDULED,
        now=scheduled_for,
    )[0]


def test_default_jobs_materialize_idempotently_and_merge_hourly_pulse(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    jobs = ensure_default_automation_jobs(
        repository,
        platform_name=PLATFORM,
        now=now,
    )

    first = AutomationSchedulePlanner(repository).materialize(now=now)
    second = AutomationSchedulePlanner(repository).materialize(now=now)

    assert len(jobs) == 8
    assert first.created_run_ids
    assert len(first.merged_run_ids) == 1
    assert second.created_run_ids == ()
    pulse = repository.list_runs(
        job_id="AUTOMATION-ONLINE-PULSE-10M"
    )[0]
    full = repository.list_runs(
        job_id="AUTOMATION-FULL-MARKET-SCAN-HOURLY"
    )[0]
    assert pulse.run_status is AutomationRunStatus.MERGED
    assert full.run_status is AutomationRunStatus.SCHEDULED
    links = repository.list_links(child_run_id=pulse.run_id)
    assert [(item.parent_run_id, item.relation_type) for item in links] == [
        (full.run_id, "MERGED_RUN")
    ]


def test_sleep_records_missed_pulses_and_only_catches_latest_window(
    repository: AutomationRepository,
) -> None:
    first_now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    pulse = _store_job(
        repository,
        _job(
            job_id="PULSE",
            job_type=ONLINE_PULSE,
            minutes=10,
            priority=60,
        ),
        now=first_now,
    )
    full = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
            priority=50,
        ),
        now=first_now,
    )
    planner = AutomationSchedulePlanner(repository)
    planner.materialize(now=first_now)

    wake_at = first_now + timedelta(hours=1, minutes=5)
    result = planner.materialize(now=wake_at)

    pulse_runs = repository.list_runs(job_id=pulse.job_id)
    full_runs = repository.list_runs(job_id=full.job_id)
    assert len(result.missed_run_ids) == 5
    assert sum(
        run.run_status is AutomationRunStatus.MISSED
        for run in pulse_runs
    ) == 5
    assert sum(
        run.run_status is AutomationRunStatus.MERGED
        for run in pulse_runs
    ) == 2
    assert len(full_runs) == 2
    assert all(
        run.run_status is AutomationRunStatus.SCHEDULED
        for run in full_runs
    )


def test_merge_never_crosses_platform_trade_date_cutoff(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 10, 6, tzinfo=timezone.utc)
    pulse_job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    pre_cutoff_job = _store_job(
        repository,
        AutomationJob(
            job_id="PRE-CUTOFF",
            job_type="PRE_CUTOFF_FULL_SCAN",
            display_name="截单前完整扫描",
            enabled=True,
            schedule_kind=DAILY_LOCAL_TIME,
            schedule_expression="17:55",
            priority=30,
            config={
                "platform_name": PLATFORM,
                "catchup_policy": "LATEST_ONLY",
            },
        ),
        now=now,
    )

    result = AutomationSchedulePlanner(repository).materialize(now=now)

    pulse = repository.list_runs(job_id=pulse_job.job_id)[0]
    pre_cutoff = repository.list_runs(job_id=pre_cutoff_job.job_id)[0]
    assert pulse.platform_trade_date != pre_cutoff.platform_trade_date
    assert result.merged_run_ids == ()
    assert pulse.run_status is AutomationRunStatus.SCHEDULED
    assert pre_cutoff.run_status is AutomationRunStatus.SCHEDULED


def test_long_sleep_is_bounded_to_prevent_task_storm(
    repository: AutomationRepository,
) -> None:
    first_now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=first_now,
    )
    planner = AutomationSchedulePlanner(
        repository,
        max_windows_per_job=4,
    )
    planner.materialize(now=first_now)

    result = planner.materialize(now=first_now + timedelta(days=1))

    assert len(result.created_run_ids) == 4
    assert len(result.missed_run_ids) == 3
    assert result.truncated_window_count == 140
    runs = repository.list_runs(job_id=job.job_id)
    assert len(runs) == 5
    events = repository.list_events(runs[-2].run_id)
    assert any(
        event.event_type == "MISSED_WINDOWS_TRUNCATED"
        for event in events
    )


def test_run_claim_is_single_owner_until_lease_expires(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    run = _ensure_run(repository, job, scheduled_for=now)

    first = repository.claim_next(
        owner_token="owner-1",
        now=now,
        lease_seconds=10,
        allowed_job_types=[ONLINE_PULSE],
    )
    overlapping = repository.claim_next(
        owner_token="owner-2",
        now=now + timedelta(seconds=5),
        lease_seconds=10,
        allowed_job_types=[ONLINE_PULSE],
    )
    reclaimed = repository.claim_next(
        owner_token="owner-2",
        now=now + timedelta(seconds=11),
        lease_seconds=10,
        allowed_job_types=[ONLINE_PULSE],
    )

    assert first is not None
    assert first.run.run_id == run.run_id
    assert overlapping is None
    assert reclaimed is not None
    assert reclaimed.reclaimed is True
    assert reclaimed.lease_version == first.lease_version + 1
    assert (
        repository.finish_run(
            first,
            AutomationRunOutcome(
                status=AutomationRunStatus.SUCCESS
            ),
            now=now + timedelta(seconds=12),
        )
        is False
    )
    assert repository.finish_run(
        reclaimed,
        AutomationRunOutcome(status=AutomationRunStatus.SUCCESS),
        now=now + timedelta(seconds=12),
    )
    events = repository.list_events(run.run_id)
    assert [event.event_type for event in events][-2:] == [
        "LEASE_RECLAIMED",
        "RUN_FINISHED",
    ]


def test_expired_handler_writeback_stops_cycle_and_restart_recovers_run(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    clock = MutableClock(now)
    job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    run = _ensure_run(repository, job, scheduled_for=now)

    def stale_handler(run, context):
        clock.value += timedelta(seconds=11)
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    first_cycle = AutomationService(
        repository,
        handlers={ONLINE_PULSE: stale_handler},
        clock=clock,
        lease_seconds=10,
        owner_token="first-instance",
    ).run_cycle()

    assert first_cycle.claimed_run_ids == (run.run_id,)
    assert first_cycle.completed_run_ids == ()
    assert first_cycle.errors == (f"LEASE_LOST:{run.run_id}",)
    assert repository.get_run(
        run.run_id
    ).run_status is AutomationRunStatus.RUNNING

    recovered_cycle = AutomationService(
        repository,
        handlers={
            ONLINE_PULSE: lambda run, context: AutomationRunOutcome(
                status=AutomationRunStatus.SUCCESS
            )
        },
        clock=clock,
        lease_seconds=10,
        owner_token="restarted-instance",
    ).run_cycle()

    assert recovered_cycle.completed_run_ids == (run.run_id,)
    assert repository.get_run(
        run.run_id
    ).run_status is AutomationRunStatus.SUCCESS


def test_handler_heartbeat_extends_fenced_lease(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    clock = MutableClock(now)
    job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    _ensure_run(repository, job, scheduled_for=now)
    heartbeats: list[bool] = []

    def handler(run, context):
        clock.value += timedelta(seconds=5)
        heartbeats.append(context.heartbeat())
        clock.value += timedelta(seconds=6)
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    service = AutomationService(
        repository,
        handlers={ONLINE_PULSE: handler},
        clock=clock,
        lease_seconds=10,
    )
    cycle = service.run_cycle()

    assert heartbeats == [True]
    assert cycle.errors == ()
    assert len(cycle.completed_run_ids) == 1
    assert repository.get_run(
        cycle.completed_run_ids[0]
    ).run_status is AutomationRunStatus.SUCCESS


def test_handler_exception_is_bounded_failed_result(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    run = _ensure_run(repository, job, scheduled_for=now)

    def failing_handler(run, context):
        raise RuntimeError("模拟扫描失败")

    service = AutomationService(
        repository,
        handlers={ONLINE_PULSE: failing_handler},
        clock=MutableClock(now),
    )
    cycle = service.run_cycle()

    assert cycle.completed_run_ids == (run.run_id,)
    stored = repository.get_run(run.run_id)
    assert stored is not None
    assert stored.run_status is AutomationRunStatus.FAILED
    assert stored.error_code == "AUTOMATION_HANDLER_FAILED"
    assert stored.error_message == "模拟扫描失败"


def test_claim_order_uses_job_priority(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    low_priority_job = _store_job(
        repository,
        _job(
            job_id="LOW",
            job_type="LOW_PRIORITY",
            priority=90,
        ),
        now=now,
    )
    high_priority_job = _store_job(
        repository,
        _job(
            job_id="HIGH",
            job_type="HIGH_PRIORITY",
            priority=10,
        ),
        now=now,
    )
    low = _ensure_run(
        repository,
        low_priority_job,
        scheduled_for=now,
    )
    high = _ensure_run(
        repository,
        high_priority_job,
        scheduled_for=now,
    )
    calls: list[str] = []

    def handler(run, context):
        calls.append(run.run_id)
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    first_cycle = AutomationService(
        repository,
        handlers={
            "LOW_PRIORITY": handler,
            "HIGH_PRIORITY": handler,
        },
        clock=MutableClock(now),
        max_runs_per_cycle=1,
    ).run_cycle()

    assert first_cycle.completed_run_ids == (high.run_id,)
    assert calls == [high.run_id]
    assert repository.get_run(
        low.run_id
    ).run_status is AutomationRunStatus.SCHEDULED


def test_full_scan_handler_can_create_idempotent_child_run_and_link(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    parent_job = _store_job(
        repository,
        _job(
            job_id="FULL",
            job_type=FULL_MARKET_SCAN,
            minutes=60,
        ),
        now=now,
    )
    child_job = _store_job(
        repository,
        _job(
            job_id="LISTING-CHILD",
            job_type=LISTING_STATUS_SCAN,
            enabled=False,
            schedule_kind=CHILD_ONLY,
        ),
        now=now,
    )
    parent = _ensure_run(repository, parent_job, scheduled_for=now)
    child_ids: list[str] = []

    def full_handler(run, context):
        child, created = context.ensure_child_run(
            child_job_id=child_job.job_id,
            relation_type="LISTING_STATUS_CHILD",
        )
        assert created is True
        replay, replay_created = context.ensure_child_run(
            child_job_id=child_job.job_id,
            relation_type="LISTING_STATUS_CHILD",
        )
        assert replay_created is False
        assert replay.run_id == child.run_id
        child_ids.append(child.run_id)
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    cycle = AutomationService(
        repository,
        handlers={FULL_MARKET_SCAN: full_handler},
        clock=MutableClock(now),
    ).run_cycle()

    assert cycle.completed_run_ids == (parent.run_id,)
    assert len(child_ids) == 1
    child = repository.get_run(child_ids[0])
    assert child is not None
    assert child.run_status is AutomationRunStatus.SCHEDULED
    links = repository.list_links(parent_run_id=parent.run_id)
    assert [(link.child_run_id, link.relation_type) for link in links] == [
        (child.run_id, "LISTING_STATUS_CHILD")
    ]


def test_unknown_reconcile_blocks_scan_dispatch_but_not_scheduling(
    repository: AutomationRepository,
    runtime_repository: SQLiteRuntimeRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    job = _store_job(
        repository,
        _job(job_id="PULSE", job_type=ONLINE_PULSE),
        now=now,
    )
    run = _ensure_run(repository, job, scheduled_for=now)
    runtime_repository.insert_task(
        Task(
            task_id="TASK-UNKNOWN",
            internal_sku="SKU-A",
            platform_name=PLATFORM,
            action_type=TaskActionType.UPDATE_PRICE,
            priority=1,
            task_status=TaskStatus.PENDING,
            created_at=now,
            origin_type=TaskOriginType.MANUAL,
            origin_ref_id="test:unknown",
            target_price=Decimal("10.00"),
            dedupe_key="TASK-UNKNOWN",
        )
    )
    runtime_repository.insert_shadowbot_operation(
        ShadowBotOperationLedger(
            operation_id="OP-UNKNOWN",
            task_id="TASK-UNKNOWN",
            platform=PLATFORM,
            product_identity={"internal_sku": "SKU-A"},
            expected_old_price=Decimal("9.00"),
            target_price=Decimal("10.00"),
            status="NEEDS_RECONCILIATION",
            created_at=now,
            updated_at=now,
        )
    )
    calls: list[str] = []

    def handler(run, context):
        calls.append(run.run_id)
        return AutomationRunOutcome(status=AutomationRunStatus.SUCCESS)

    cycle = AutomationService(
        repository,
        handlers={ONLINE_PULSE: handler},
        clock=MutableClock(now),
    ).run_cycle()

    assert calls == []
    assert cycle.blocked_reason == "UNKNOWN_OR_RECONCILE_ACTIVE"
    assert repository.get_run(
        run.run_id
    ).run_status is AutomationRunStatus.SCHEDULED


def test_heartbeat_is_atomic_utf8_json(tmp_path: Path) -> None:
    path = tmp_path / "自动化服务" / "heartbeat.json"
    store = AutomationHeartbeatStore(path)
    payload = {
        "schema_version": "automation-heartbeat-1.0",
        "status": "RUNNING",
        "说明": "调度健康",
    }

    store.write(payload)

    assert store.read() == payload
    assert json.loads(path.read_text(encoding="utf-8"))["说明"] == "调度健康"
    assert list(path.parent.glob("*.tmp")) == []


def test_process_file_lock_rejects_second_instance(tmp_path: Path) -> None:
    lock_path = tmp_path / "automation.lock"
    with ProcessFileLock(lock_path):
        with pytest.raises(RuntimeError, match="已有实例"):
            with ProcessFileLock(lock_path):
                pass


def test_once_cli_writes_stopped_heartbeat_and_default_jobs(
    tmp_path: Path,
) -> None:
    runtime_db = tmp_path / "runtime.sqlite3"
    heartbeat = tmp_path / "heartbeat.json"
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_automation_service.py",
            "--runtime-db",
            str(runtime_db),
            "--heartbeat",
            str(heartbeat),
            "--once",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(heartbeat.read_text(encoding="utf-8"))
    assert payload["status"] == "STOPPED"
    assert payload["mode"] == "SCHEDULER_ONLY"
    runtime_repository = SQLiteRuntimeRepository(runtime_db)
    stored_jobs = AutomationRepository(runtime_repository).list_jobs()
    assert len(stored_jobs) == 8
    assert stored_jobs[0].display_name


def test_job_static_identity_is_immutable_for_existing_job_id(
    repository: AutomationRepository,
) -> None:
    now = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)
    original = _job(job_id="STABLE", job_type=ONLINE_PULSE)
    _store_job(repository, original, now=now)

    with pytest.raises(ValueError, match="cannot change job_type"):
        _store_job(
            repository,
            _job(job_id="STABLE", job_type=FULL_MARKET_SCAN),
            now=now,
        )
    with pytest.raises(ValueError, match="cannot change schedule"):
        _store_job(
            repository,
            _job(
                job_id="STABLE",
                job_type=ONLINE_PULSE,
                minutes=60,
            ),
            now=now,
        )

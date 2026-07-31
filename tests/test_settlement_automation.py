from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.enums import AutomationRunStatus, DataQualityLevel, SummaryStatus
from app.repositories.automation_repository import AutomationRepository
from app.repositories.operational_summary_repository import (
    OperationalSummaryRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import AutomationService, ensure_default_automation_jobs
from app.services.operational_time import OperationalTimeService
from app.services.settlement_automation import build_sales_settlement_handlers
from app.services.trade_day_summary import build_summary_series_id


def test_settlement_handler_uses_existing_run_lease_and_creates_only_provisional(
    tmp_path: Path,
) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    automation_repository = AutomationRepository(runtime)
    scheduled_for = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    jobs = ensure_default_automation_jobs(
        automation_repository,
        platform_name="platform",
        now=scheduled_for,
    )
    job = next(
        item
        for item in jobs
        if item.job_type == "PLATFORM_TRADE_DAY_SETTLEMENT"
    )
    time_service = OperationalTimeService(
        policies=automation_repository.load_operational_time_policies()
    )
    run, _ = automation_repository.ensure_run(
        job=job,
        scheduled_for=scheduled_for,
        time_context=time_service.classify(scheduled_for),
        initial_status=AutomationRunStatus.SCHEDULED,
        now=scheduled_for,
    )
    handlers = build_sales_settlement_handlers(
        runtime_repository=runtime,
        platform_name="platform",
    )
    cycle = AutomationService(
        automation_repository,
        handlers={
            "PLATFORM_TRADE_DAY_SETTLEMENT": handlers[
                "PLATFORM_TRADE_DAY_SETTLEMENT"
            ]
        },
        operational_time=time_service,
        clock=lambda: scheduled_for + timedelta(minutes=1),
        owner_token="settlement-test",
        max_runs_per_cycle=1,
    ).run_cycle()

    assert cycle.completed_run_ids == (run.run_id,)
    completed = automation_repository.get_run(run.run_id)
    assert completed is not None
    assert completed.run_status is AutomationRunStatus.SUCCESS
    assert completed.input_manifest_sha256.startswith("sha256:")
    assert completed.output_manifest_sha256 == completed.input_manifest_sha256

    target_date = run.platform_trade_date - timedelta(days=1)
    series_id = build_summary_series_id(
        platform_name="platform",
        platform_trade_date=target_date,
        scope_type="PLATFORM",
        scope_key="platform",
    )
    summary = OperationalSummaryRepository(runtime).get_current_summary(
        series_id
    )
    assert summary is not None
    assert summary.summary_status is SummaryStatus.PROVISIONAL
    assert summary.quality_level is DataQualityLevel.UNAVAILABLE
    assert summary.sold_qty is None
    events = automation_repository.list_events(run.run_id)
    completed_event = next(
        event for event in events if event.event_type == "RUN_FINISHED"
    )
    assert completed_event.payload["platform_write_performed"] is False


def test_plan_input_handler_registers_no_platform_write_side_effect(
    tmp_path: Path,
) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    automation_repository = AutomationRepository(runtime)
    scheduled_for = datetime(2026, 8, 1, 12, 5, tzinfo=timezone.utc)
    jobs = ensure_default_automation_jobs(
        automation_repository,
        platform_name="platform",
        now=scheduled_for,
    )
    job = next(item for item in jobs if item.job_type == "SALES_PLAN_INPUT_BUILD")
    time_service = OperationalTimeService(
        policies=automation_repository.load_operational_time_policies()
    )
    run, _ = automation_repository.ensure_run(
        job=job,
        scheduled_for=scheduled_for,
        time_context=time_service.classify(scheduled_for),
        initial_status=AutomationRunStatus.SCHEDULED,
        now=scheduled_for,
    )
    handlers = build_sales_settlement_handlers(
        runtime_repository=runtime,
        platform_name="platform",
    )
    AutomationService(
        automation_repository,
        handlers={"SALES_PLAN_INPUT_BUILD": handlers["SALES_PLAN_INPUT_BUILD"]},
        operational_time=time_service,
        clock=lambda: scheduled_for + timedelta(minutes=1),
        owner_token="plan-test",
        max_runs_per_cycle=1,
    ).run_cycle()

    completed = automation_repository.get_run(run.run_id)
    assert completed is not None
    assert completed.run_status is AutomationRunStatus.SUCCESS
    assert completed.output_manifest_sha256.startswith("sha256:")
    completed_event = next(
        event
        for event in automation_repository.list_events(run.run_id)
        if event.event_type == "RUN_FINISHED"
    )
    assert completed_event.payload["prediction_performed"] is False
    assert completed_event.payload["platform_write_performed"] is False

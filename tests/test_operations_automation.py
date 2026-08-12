from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from openpyxl import Workbook

from app.enums import AutomationRunStatus, ReviewTaskStatus
from app.models import ReviewTask
from app.repositories.automation_repository import AutomationRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import (
    LISTING_RULE_HEADERS,
    PRICE_RULE_HEADERS,
    PRODUCT_HEADERS,
)
from app.services.automation import (
    DAILY_TASK_GENERATION,
    REVIEW_TIMEOUT_MAINTENANCE,
    SALES_PLAN_INPUT_BUILD,
    ensure_default_automation_jobs,
)
from app.services.operations_automation import (
    DailyTaskGenerationAutomationHandler,
    ReviewTimeoutAutomationHandler,
)
from app.services.operational_time import OperationalTimeService


NOW = datetime(2026, 8, 12, 12, 10, tzinfo=timezone.utc)
PLATFORM = "测试平台"


class FakeContext:
    def __init__(self, now: datetime) -> None:
        self._now = now
        self.bound_manifest = ""

    def heartbeat(self) -> bool:
        return True

    def clock(self) -> datetime:
        return self._now

    def bind_input_manifest(self, manifest_sha256: str):
        self.bound_manifest = manifest_sha256
        return None


def _write_workbook(path: Path, headers, rows) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def _runtime(tmp_path):
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    automation = AutomationRepository(runtime)
    jobs = ensure_default_automation_jobs(
        automation,
        platform_name=PLATFORM,
        now=NOW,
    )
    return runtime, automation, {job.job_type: job for job in jobs}


def _run(automation, job, scheduled_for):
    context = OperationalTimeService(
        policies=automation.load_operational_time_policies()
    ).classify(scheduled_for)
    return automation.ensure_run(
        job=job,
        scheduled_for=scheduled_for,
        time_context=context,
        initial_status=AutomationRunStatus.SCHEDULED,
        now=scheduled_for,
    )[0]


def test_review_timeout_handler_uses_existing_review_service(tmp_path):
    runtime, automation, jobs = _runtime(tmp_path)
    review = ReviewTask(
        review_task_id="REVIEW-OVERDUE",
        trade_date=date(2026, 8, 12),
        scope_type="sku",
        scope_key="SKU-001",
        dedupe_key="review-overdue",
        source_task_id=None,
        review_type="product_mapping",
        review_status=ReviewTaskStatus.PENDING,
        internal_sku="SKU-001",
        platform_name=PLATFORM,
        reason="mapping review",
        required_by=NOW - timedelta(minutes=1),
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(hours=1),
    )
    runtime.insert_review_tasks([review])
    run = _run(automation, jobs[REVIEW_TIMEOUT_MAINTENANCE], NOW)

    outcome = ReviewTimeoutAutomationHandler(runtime)(run, FakeContext(NOW))

    assert outcome.status is AutomationRunStatus.SUCCESS
    stored = runtime.get_review_task(review.review_task_id)
    assert stored is not None
    assert stored.review_status is ReviewTaskStatus.EXPIRED
    assert outcome.event_payload["platform_write_performed"] is False


def test_daily_generation_skips_until_same_trade_day_plan_input_succeeds(tmp_path):
    runtime, automation, jobs = _runtime(tmp_path)
    run = _run(automation, jobs[DAILY_TASK_GENERATION], NOW)
    handler = DailyTaskGenerationAutomationHandler(
        runtime,
        automation,
        tmp_path / "missing-products.xlsx",
        tmp_path / "missing-price-rules.xlsx",
        tmp_path / "missing-listing-rules.xlsx",
    )

    outcome = handler(run, FakeContext(NOW))

    assert outcome.status is AutomationRunStatus.SKIPPED
    assert outcome.error_code == "PLAN_INPUT_NOT_READY"
    assert runtime.list_tasks() == []


def test_daily_generation_reuses_rule_workflow_after_plan_input(tmp_path):
    runtime, automation, jobs = _runtime(tmp_path)
    products = tmp_path / "products.xlsx"
    price_rules = tmp_path / "price_rules.xlsx"
    listing_rules = tmp_path / "listing_rules.xlsx"
    _write_workbook(
        products,
        PRODUCT_HEADERS,
        [["SKU-001", "艾莎", "A", "70", "扎", 10, 50, True, 14, 15, "", "spring", "red"]],
    )
    _write_workbook(
        price_rules,
        PRICE_RULE_HEADERS,
        [["RULE-1", "固定加价", "*", "*", PLATFORM, "fixed_markup", 5, 14, "round", "", True, 10, ""]],
    )
    _write_workbook(
        listing_rules,
        LISTING_RULE_HEADERS,
        [["LIST-1", "库存恢复允许上架", "*", "*", PLATFORM, 10, "stock_above_online", True, 5, ""]],
    )
    plan_time = NOW - timedelta(minutes=5)
    plan_context = OperationalTimeService(
        policies=automation.load_operational_time_policies()
    ).classify(plan_time)
    automation.ensure_run(
        job=jobs[SALES_PLAN_INPUT_BUILD],
        scheduled_for=plan_time,
        time_context=plan_context,
        initial_status=AutomationRunStatus.SUCCESS,
        now=plan_time,
    )
    daily_job = replace(
        jobs[DAILY_TASK_GENERATION],
        config={
            **jobs[DAILY_TASK_GENERATION].config,
            "source_allowlist": ["PRODUCTS", "PRICE_RULES", "LISTING_RULES"],
        },
    )
    automation.upsert_job(daily_job, now=NOW)
    run = _run(automation, daily_job, NOW)
    context = FakeContext(NOW)

    outcome = DailyTaskGenerationAutomationHandler(
        runtime,
        automation,
        products,
        price_rules,
        listing_rules,
    )(run, context)

    assert outcome.status is AutomationRunStatus.SUCCESS
    assert context.bound_manifest.startswith("sha256:")
    assert outcome.event_payload["platform_write_performed"] is False
    assert outcome.event_payload["source_allowlist"] == [
        "LISTING_RULES",
        "PRICE_RULES",
        "PRODUCTS",
    ]
    assert outcome.event_payload["inserted_task_count"] <= outcome.event_payload[
        "candidate_task_count"
    ]

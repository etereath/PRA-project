from __future__ import annotations

import json
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.automation_models import AutomationRun
from app.enums import (
    AutomationRunStatus,
    DataQualityLevel,
    FactSource,
    SellerPhase,
    SummaryStatus,
)
from app.repositories.operational_summary_repository import (
    OperationalSummaryRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.operational_time import (
    DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
)
from app.services.trade_day_settlement import TradeDaySettlementService


PLATFORM = "platform"
TRADE_DATE = date(2026, 7, 31)
BASE = datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def settlement(
    tmp_path: Path,
) -> tuple[
    TradeDaySettlementService,
    OperationalSummaryRepository,
    SQLiteRuntimeRepository,
]:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    repository = OperationalSummaryRepository(runtime)
    return TradeDaySettlementService(repository), repository, runtime


def _insert_order_snapshot(
    runtime: SQLiteRuntimeRepository,
    batch_id: str,
    *,
    completed_at: datetime,
    rows: tuple[tuple[str, int, str, str | None, str], ...],
    trade_day_status: str = "CLOSED",
    stored_batch_status: str = "ACCEPTED",
    source_batch_status: str = "ACCEPTED",
    scope_complete: bool = True,
    end_marker_verified: bool = True,
) -> None:
    job_id = f"job-{batch_id}"
    run_id = f"run-{batch_id}"
    content_sha256 = "sha256:" + hashlib_for(batch_id)
    now = completed_at.isoformat()
    requested_range = json.dumps(
        {
            "source_batch_status": source_batch_status,
            "accepted_mapping_version": "mapping-v1",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with closing(runtime.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO automation_jobs(
                job_id, job_type, display_name, enabled,
                schedule_kind, schedule_expression, priority,
                config_json, created_at, updated_at
            ) VALUES (?, 'ORDER_SCAN', ?, 0, 'CHILD_ONLY', '-', 51, '{}', ?, ?)
            """,
            (job_id, job_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO automation_runs(
                run_id, job_id, job_type, logical_run_key, run_status,
                platform_name, platform_trade_date, seller_operation_date,
                seller_phase, time_policy_version, scheduled_for,
                created_at, updated_at
            ) VALUES (
                ?, ?, 'ORDER_SCAN', ?, 'SUCCESS', ?, ?, ?,
                'NORMAL_SALES', ?, ?, ?, ?
            )
            """,
            (
                run_id,
                job_id,
                f"logical-{batch_id}",
                PLATFORM,
                TRADE_DATE.isoformat(),
                TRADE_DATE.isoformat(),
                DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
                now,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO order_observation_batches(
                observation_batch_id, automation_run_id, platform_name,
                requested_platform_trade_date, trade_day_status,
                capability_result, batch_status, scan_started_at,
                scan_completed_at, requested_range_json, scope_complete,
                end_marker_verified, content_sha256, time_policy_version,
                error_code, error_message, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, 'SUCCEEDED', ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?
            )
            """,
            (
                batch_id,
                run_id,
                PLATFORM,
                TRADE_DATE.isoformat(),
                trade_day_status,
                stored_batch_status,
                (completed_at - timedelta(minutes=1)).isoformat(),
                now,
                requested_range,
                int(scope_complete),
                int(end_marker_verified),
                content_sha256,
                DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
                now,
            ),
        )
        for occurrence_no, (
            fingerprint,
            qty,
            amount,
            internal_sku,
            mapping_status,
        ) in enumerate(rows, start=1):
            connection.execute(
                """
                INSERT INTO order_observation_items(
                    observation_item_id, observation_batch_id,
                    platform_name, platform_trade_date, trade_day_status,
                    order_identity_fingerprint, occurrence_no,
                    order_created_at, platform_product_name, grade,
                    internal_sku, mapping_status, mapping_version,
                    order_qty, order_transaction_amount, observed_at,
                    seller_operation_date, seller_phase,
                    raw_observation_sha256
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, 'Rose', 'B', ?, ?, 'mapping-v1',
                    ?, ?, ?, ?, 'NORMAL_SALES', ?
                )
                """,
                (
                    f"item-{batch_id}-{occurrence_no}",
                    batch_id,
                    PLATFORM,
                    TRADE_DATE.isoformat(),
                    trade_day_status,
                    fingerprint,
                    occurrence_no,
                    (BASE + timedelta(minutes=occurrence_no)).isoformat(),
                    internal_sku,
                    mapping_status,
                    qty,
                    amount,
                    now,
                    TRADE_DATE.isoformat(),
                    hashlib_for(f"raw-{batch_id}-{occurrence_no}"),
                ),
            )


def _create_provisional(service: TradeDaySettlementService):
    return service.create_provisional(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
        seller_operation_date=date(2026, 8, 1),
        seller_phase=SellerPhase.NORMAL_SALES,
        time_policy_version=DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
        scope_type="PLATFORM",
        scope_key=PLATFORM,
        changed_by="test",
    )


def test_complete_closed_order_lifecycle_never_skips_provisional(
    settlement,
) -> None:
    service, repository, runtime = settlement
    _insert_order_snapshot(
        runtime,
        "batch-1",
        completed_at=BASE + timedelta(hours=2),
        rows=(
            ("fingerprint-a", 2, "24", "SKU-1", "VERIFIED"),
            ("fingerprint-b", 1, "9", "SKU-2", "VERIFIED"),
        ),
    )

    provisional = _create_provisional(service)
    assert provisional.summary.summary_status is SummaryStatus.PROVISIONAL
    assert provisional.summary.fact_source is FactSource.ORDER_OBSERVED
    assert provisional.summary.quality_level is DataQualityLevel.ORDER_COMPLETE
    assert provisional.summary.sold_qty == 3
    assert provisional.summary.order_count == 2
    assert provisional.summary.transaction_amount_total == Decimal("33")

    observed = service.observe(
        provisional.summary.summary_id,
        changed_by="test",
    )
    assert observed.summary.summary_status is SummaryStatus.OBSERVED
    reconciled = service.reconcile(
        provisional.summary.summary_id,
        changed_by="test",
    )
    assert reconciled.summary.summary_status is SummaryStatus.RECONCILED
    assert reconciled.summary.quality_reason == "RECONCILED:NO_COMPARABLE_ESTIMATE"
    final = service.finalize(
        provisional.summary.summary_id,
        changed_by="test",
    )
    assert final.summary.summary_status is SummaryStatus.FINAL
    assert final.summary.sold_qty == 3
    assert [
        event.to_status
        for event in repository.list_events(final.summary.summary_id)
    ] == [
        SummaryStatus.PROVISIONAL,
        SummaryStatus.OBSERVED,
        SummaryStatus.RECONCILED,
        SummaryStatus.FINAL,
    ]


def test_latest_complete_snapshot_replaces_earlier_snapshot_not_sums(
    settlement,
) -> None:
    service, _, runtime = settlement
    _insert_order_snapshot(
        runtime,
        "batch-1",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 2, "24", "SKU-1", "VERIFIED"),),
    )
    _insert_order_snapshot(
        runtime,
        "batch-2",
        completed_at=BASE + timedelta(hours=2),
        rows=(("fingerprint-b", 3, "36", "SKU-1", "VERIFIED"),),
    )
    summary = _create_provisional(service).summary
    assert summary.sold_qty == 3
    assert summary.order_count == 1
    assert summary.transaction_amount_total == Decimal("36")


def test_platform_total_can_be_complete_despite_mapping_gap(
    settlement,
) -> None:
    service, _, runtime = settlement
    _insert_order_snapshot(
        runtime,
        "batch-1",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 2, "24", None, "UNMAPPED"),),
        stored_batch_status="PARTIAL",
        source_batch_status="ACCEPTED",
    )
    platform = _create_provisional(service).summary
    assert platform.quality_level is DataQualityLevel.ORDER_COMPLETE
    assert platform.sold_qty == 2

    sku = service.create_provisional(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
        seller_operation_date=date(2026, 8, 1),
        seller_phase=SellerPhase.NORMAL_SALES,
        time_policy_version=DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
        scope_type="SKU",
        scope_key="SKU-1",
        changed_by="test",
    ).summary
    assert sku.quality_level is DataQualityLevel.ORDER_PARTIAL
    assert sku.sold_qty == 0


def test_trusted_empty_closed_page_is_complete_zero_not_unavailable(
    settlement,
) -> None:
    service, _, runtime = settlement
    _insert_order_snapshot(
        runtime,
        "batch-empty",
        completed_at=BASE + timedelta(hours=1),
        rows=(),
    )
    summary = _create_provisional(service).summary
    assert summary.quality_level is DataQualityLevel.ORDER_COMPLETE
    assert summary.sold_qty == 0
    assert summary.order_count == 0
    assert summary.transaction_amount_total == Decimal("0")


def test_final_rejects_new_authoritative_snapshot_after_reconciliation(
    settlement,
) -> None:
    service, _, runtime = settlement
    _insert_order_snapshot(
        runtime,
        "batch-1",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 2, "24", "SKU-1", "VERIFIED"),),
    )
    summary_id = _create_provisional(service).summary.summary_id
    service.observe(summary_id, changed_by="test")
    service.reconcile(summary_id, changed_by="test")
    _insert_order_snapshot(
        runtime,
        "batch-2",
        completed_at=BASE + timedelta(hours=2),
        rows=(("fingerprint-b", 3, "36", "SKU-1", "VERIFIED"),),
    )

    with pytest.raises(ValueError, match="changed after reconciliation"):
        service.finalize(summary_id, changed_by="test")


def test_cancellation_is_explanation_and_is_not_subtracted_twice(
    settlement,
) -> None:
    service, _, runtime = settlement
    _insert_order_snapshot(
        runtime,
        "batch-1",
        completed_at=BASE + timedelta(hours=1),
        rows=(
            ("same", 1, "12", "SKU-1", "VERIFIED"),
            ("same", 1, "12", "SKU-1", "VERIFIED"),
        ),
    )
    _insert_order_snapshot(
        runtime,
        "batch-2",
        completed_at=BASE + timedelta(hours=2),
        rows=(("same", 1, "12", "SKU-1", "VERIFIED"),),
    )
    summary_id = _create_provisional(service).summary.summary_id
    service.observe(summary_id, changed_by="test")
    service.reconcile(summary_id, changed_by="test")
    final = service.finalize(summary_id, changed_by="test").summary
    assert final.sold_qty == 1
    assert final.order_count == 1
    assert final.transaction_amount_total == Decimal("12")


def test_open_order_snapshot_can_be_partial_but_never_final(
    settlement,
) -> None:
    service, _, runtime = settlement
    _insert_order_snapshot(
        runtime,
        "batch-open",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 2, "24", "SKU-1", "VERIFIED"),),
        trade_day_status="OPEN",
    )
    summary = _create_provisional(service).summary
    assert summary.quality_level is DataQualityLevel.ORDER_PARTIAL
    service.observe(summary.summary_id, changed_by="test")
    service.reconcile(summary.summary_id, changed_by="test")
    with pytest.raises(ValueError, match="complete CLOSED"):
        service.finalize(summary.summary_id, changed_by="test")


def test_automation_settlement_builds_grade_variety_sku_and_hour_scopes(
    settlement,
) -> None:
    _, repository, runtime = settlement
    _insert_order_snapshot(
        runtime,
        "batch-1",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 2, "24", "SKU-1", "VERIFIED"),),
    )
    with closing(runtime.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO listing_status(
                listing_status_id, platform_name, internal_sku,
                variety, grade, current_price, updated_at
            ) VALUES ('listing-1', ?, 'SKU-1', 'Rose', 'B', '12', ?)
            """,
            (PLATFORM, BASE.isoformat()),
        )
    service = TradeDaySettlementService(
        repository,
        sku_dimensions=repository.list_sku_dimensions(
            platform_name=PLATFORM
        ),
    )
    run = AutomationRun(
        run_id="settlement-run",
        job_id="settlement-job",
        job_type="PLATFORM_TRADE_DAY_SETTLEMENT",
        logical_run_key="settlement-key",
        run_status=AutomationRunStatus.RUNNING,
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE + timedelta(days=1),
        seller_operation_date=TRADE_DATE + timedelta(days=1),
        seller_phase=SellerPhase.NORMAL_SALES,
        time_policy_version=DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
        scheduled_for=BASE + timedelta(days=1),
    )

    results = service.create_provisionals_for_run(run)
    scopes = {
        (result.summary.scope_type, result.summary.scope_key)
        for result in results
    }
    assert ("PLATFORM", PLATFORM) in scopes
    assert ("GRADE", "B") in scopes
    assert ("VARIETY", "Rose") in scopes
    assert ("SKU", "SKU-1") in scopes
    assert len(
        [scope for scope in scopes if scope[0] == "TIME_BUCKET"]
    ) == 24
    assert all(
        result.summary.summary_status is SummaryStatus.PROVISIONAL
        for result in results
    )


def hashlib_for(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()

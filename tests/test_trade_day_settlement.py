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
from app.models import Product
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.operational_summary_repository import (
    OperationalSummaryRepository,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.sales_settlement_models import SalesEstimateSegment
from app.services.sales_estimate import (
    SALES_ESTIMATE_ALGORITHM_VERSION,
    SalesEstimateService,
)
from app.services.authoritative_inventory import (
    InventoryApplicationService,
    InventorySalesApplicationService,
    sqlite_logical_snapshot_sha256,
)
from app.services.operational_time import (
    DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
)
from app.services.trade_day_settlement import TradeDaySettlementService
from tests.inventory_cutover_support import insert_cutover_order_snapshot


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
    platform_trade_date: date = TRADE_DATE,
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
                platform_trade_date.isoformat(),
                platform_trade_date.isoformat(),
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
                platform_trade_date.isoformat(),
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
                    platform_trade_date.isoformat(),
                    trade_day_status,
                    fingerprint,
                    occurrence_no,
                    (BASE + timedelta(minutes=occurrence_no)).isoformat(),
                    internal_sku,
                    mapping_status,
                    qty,
                    amount,
                    now,
                    platform_trade_date.isoformat(),
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


def test_final_rejects_estimate_added_after_reconciliation(
    settlement,
) -> None:
    service, repository, runtime = settlement
    _insert_order_snapshot(
        runtime,
        "batch-1",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 2, "24", "SKU-1", "VERIFIED"),),
    )
    summary_id = _create_provisional(service).summary.summary_id
    service.observe(summary_id, changed_by="test")
    service.reconcile(summary_id, changed_by="test")
    _append_complete_estimate_day(repository, sold_qty=2)

    with pytest.raises(ValueError, match="estimate evidence changed"):
        service.finalize(summary_id, changed_by="test")


def test_final_rejects_new_selected_estimate_algorithm(
    settlement,
) -> None:
    service, repository, runtime = settlement
    _insert_order_snapshot(
        runtime,
        "batch-1",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 2, "24", "SKU-1", "VERIFIED"),),
    )
    _append_complete_estimate_day(repository, sold_qty=2)
    summary_id = _create_provisional(service).summary.summary_id
    service.observe(summary_id, changed_by="test")
    service.reconcile(summary_id, changed_by="test")
    _append_complete_estimate_day(
        repository,
        sold_qty=2,
        algorithm_version="sales-estimate-v2",
    )
    revised_algorithm_service = TradeDaySettlementService(
        repository,
        estimate_service=SalesEstimateService(
            algorithm_version="sales-estimate-v2"
        ),
    )

    with pytest.raises(ValueError, match="estimate evidence changed"):
        revised_algorithm_service.finalize(summary_id, changed_by="test")


def test_historical_order_backfill_supersedes_only_final_and_is_idempotent(
    settlement,
) -> None:
    service, repository, runtime = settlement
    _insert_order_snapshot(
        runtime,
        "batch-1",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 2, "24", "SKU-1", "VERIFIED"),),
    )
    old_id = _create_provisional(service).summary.summary_id
    service.observe(old_id, changed_by="test")
    service.reconcile(old_id, changed_by="test")
    old_final = service.finalize(old_id, changed_by="test").summary
    _insert_order_snapshot(
        runtime,
        "batch-2",
        completed_at=BASE + timedelta(hours=2),
        rows=(("fingerprint-b", 3, "36", "SKU-1", "VERIFIED"),),
    )
    with closing(runtime.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO listing_status(
                listing_status_id, platform_name, internal_sku,
                variety, grade, current_price, updated_at
            ) VALUES (
                'listing-backfill', ?, 'SKU-1', 'Rose', 'B', '12', ?
            )
            """,
            (PLATFORM, BASE.isoformat()),
        )

    refreshed = service.refresh_after_order_import(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
        observation_batch_id="batch-2",
    )
    by_scope = {
        (result.summary.scope_type, result.summary.scope_key): result
        for result in refreshed
    }
    assert {"PLATFORM", "VARIETY", "GRADE", "SKU", "TIME_BUCKET"} == {
        scope_type for scope_type, _ in by_scope
    }
    assert len(
        [scope for scope in by_scope if scope[0] == "TIME_BUCKET"]
    ) == 24
    revision = by_scope[("PLATFORM", PLATFORM)].summary
    assert revision.summary_status is SummaryStatus.OBSERVED
    assert revision.version_no == old_final.version_no + 1
    assert revision.supersedes_summary_id == old_final.summary_id
    assert revision.sold_qty == 3
    persisted_old = repository.get_summary(old_final.summary_id)
    assert persisted_old is not None
    assert persisted_old.summary_status is SummaryStatus.FINAL
    assert persisted_old.sold_qty == 2
    assert not persisted_old.is_current

    replay = service.refresh_after_order_import(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
        observation_batch_id="batch-2",
    )
    replay_by_scope = {
        (result.summary.scope_type, result.summary.scope_key): result
        for result in replay
    }
    assert replay_by_scope.keys() == by_scope.keys()
    assert all(not result.changed for result in replay)
    assert (
        replay_by_scope[("PLATFORM", PLATFORM)].summary.summary_id
        == revision.summary_id
    )


def test_non_final_order_backfill_refreshes_same_version(
    settlement,
) -> None:
    service, _, runtime = settlement
    _insert_order_snapshot(
        runtime,
        "batch-1",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 2, "24", "SKU-1", "VERIFIED"),),
    )
    observed = service.observe(
        _create_provisional(service).summary.summary_id,
        changed_by="test",
    ).summary
    _insert_order_snapshot(
        runtime,
        "batch-2",
        completed_at=BASE + timedelta(hours=2),
        rows=(("fingerprint-b", 3, "36", "SKU-1", "VERIFIED"),),
    )

    refreshed = service.refresh_after_order_import(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
        observation_batch_id="batch-2",
    )[0].summary
    assert refreshed.summary_id == observed.summary_id
    assert refreshed.version_no == observed.version_no
    assert refreshed.supersedes_summary_id is None
    assert refreshed.summary_status is SummaryStatus.OBSERVED
    assert refreshed.sold_qty == 3


def test_historical_backfill_rolls_back_all_scope_revisions(
    settlement,
) -> None:
    service, repository, runtime = settlement
    _insert_order_snapshot(
        runtime,
        "batch-before-atomic",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 2, "24", "SKU-1", "VERIFIED"),),
    )
    final_ids = {}
    for scope_type, scope_key in (
        ("PLATFORM", PLATFORM),
        ("SKU", "SKU-1"),
    ):
        provisional = service.create_provisional(
            platform_name=PLATFORM,
            platform_trade_date=TRADE_DATE,
            seller_operation_date=date(2026, 8, 1),
            seller_phase=SellerPhase.NORMAL_SALES,
            time_policy_version=DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
            scope_type=scope_type,
            scope_key=scope_key,
            changed_by="test",
        ).summary
        service.observe(provisional.summary_id, changed_by="test")
        service.reconcile(provisional.summary_id, changed_by="test")
        final = service.finalize(
            provisional.summary_id,
            changed_by="test",
        ).summary
        final_ids[(scope_type, scope_key)] = final.summary_id

    _insert_order_snapshot(
        runtime,
        "batch-after-atomic",
        completed_at=BASE + timedelta(hours=2),
        rows=(("fingerprint-b", 3, "36", "SKU-1", "VERIFIED"),),
    )
    validations = 0

    def fail_before_second_final_scope(_connection) -> None:
        nonlocal validations
        validations += 1
        if validations == 3:
            raise RuntimeError("injected backfill scope failure")

    with pytest.raises(RuntimeError, match="injected backfill scope failure"):
        service.refresh_after_order_import(
            platform_name=PLATFORM,
            platform_trade_date=TRADE_DATE,
            observation_batch_id="batch-after-atomic",
            transaction_validator=fail_before_second_final_scope,
        )

    current = {
        (summary.scope_type, summary.scope_key): summary
        for summary in repository.list_current_summaries(
            platform_name=PLATFORM,
            platform_trade_date=TRADE_DATE,
        )
    }
    assert set(current) == set(final_ids)
    for scope, summary_id in final_ids.items():
        assert current[scope].summary_id == summary_id
        assert current[scope].summary_status is SummaryStatus.FINAL


def test_scan_only_settlement_builds_all_supported_scopes_from_fresh_dimensions(
    settlement,
) -> None:
    _, repository, runtime = settlement
    _append_complete_estimate_day(repository, sold_qty=4)
    with closing(runtime.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO listing_status(
                listing_status_id, platform_name, internal_sku,
                variety, grade, current_price, updated_at
            ) VALUES ('listing-scan-only', ?, 'SKU-1', 'Rose', 'B', '12', ?)
            """,
            (PLATFORM, BASE.isoformat()),
        )
    service = TradeDaySettlementService(repository)
    run = AutomationRun(
        run_id="settlement-scan-only",
        job_id="settlement-job",
        job_type="PLATFORM_TRADE_DAY_SETTLEMENT",
        logical_run_key="settlement-scan-only-key",
        run_status=AutomationRunStatus.RUNNING,
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE + timedelta(days=1),
        seller_operation_date=TRADE_DATE + timedelta(days=1),
        seller_phase=SellerPhase.NORMAL_SALES,
        time_policy_version=DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
        scheduled_for=BASE + timedelta(days=1),
    )

    results = service.create_provisionals_for_run(run)
    by_scope = {
        (item.summary.scope_type, item.summary.scope_key): item.summary
        for item in results
    }
    assert by_scope[("PLATFORM", PLATFORM)].sold_qty == 4
    assert by_scope[("VARIETY", "Rose")].sold_qty == 4
    assert by_scope[("GRADE", "B")].sold_qty == 4
    assert by_scope[("SKU", "SKU-1")].sold_qty == 4
    assert len(
        [scope for scope in by_scope if scope[0] == "TIME_BUCKET"]
    ) == 24


def test_multi_scope_settlement_rolls_back_as_one_transaction(
    settlement,
) -> None:
    _, repository, runtime = settlement
    _insert_order_snapshot(
        runtime,
        "batch-atomic",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 2, "24", "SKU-1", "VERIFIED"),),
    )
    service = TradeDaySettlementService(repository)
    run = AutomationRun(
        run_id="settlement-atomic",
        job_id="settlement-job",
        job_type="PLATFORM_TRADE_DAY_SETTLEMENT",
        logical_run_key="settlement-atomic-key",
        run_status=AutomationRunStatus.RUNNING,
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE + timedelta(days=1),
        seller_operation_date=TRADE_DATE + timedelta(days=1),
        seller_phase=SellerPhase.NORMAL_SALES,
        time_policy_version=DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
        scheduled_for=BASE + timedelta(days=1),
    )
    validations = 0

    def fail_second_scope(_connection) -> None:
        nonlocal validations
        validations += 1
        if validations == 2:
            raise RuntimeError("injected multi-scope failure")

    with pytest.raises(RuntimeError, match="injected multi-scope failure"):
        service.create_provisionals_for_run(
            run,
            transaction_validator=fail_second_scope,
        )

    assert repository.list_current_summaries(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
    ) == ()


def _append_complete_estimate_day(
    repository: OperationalSummaryRepository,
    *,
    sold_qty: int,
    platform_trade_date: date = TRADE_DATE,
    algorithm_version: str = SALES_ESTIMATE_ALGORITHM_VERSION,
    revision: int = 0,
    quality_level: DataQualityLevel = DataQualityLevel.SCAN_ESTIMATED_HIGH,
) -> None:
    started_at = datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc) + timedelta(
        days=(platform_trade_date - TRADE_DATE).days
    )
    for index in range(96):
        interval_start = started_at + timedelta(minutes=15 * index)
        interval_end = interval_start + timedelta(minutes=15)
        repository.append_estimate_segment(
            SalesEstimateSegment(
                estimate_segment_id=(
                    f"estimate-{platform_trade_date.isoformat()}-"
                    f"{algorithm_version}-r{revision}-{index:03}"
                ),
                platform_name=PLATFORM,
                internal_sku="SKU-1",
                platform_trade_date=platform_trade_date,
                interval_started_at=interval_start,
                interval_ended_at=interval_end,
                inventory_before=100,
                inventory_after=100 - sold_qty if index == 0 else 100,
                known_inventory_adjustment=0,
                known_adjustment_source_refs=(),
                estimated_sold_qty=sold_qty if index == 0 else 0,
                estimation_eligible=True,
                estimation_reason="ELIGIBLE_NO_ADJUSTMENT",
                quality_level=quality_level,
                mapping_version="mapping-v1",
                supporting_observation_ids=(
                    f"before-{algorithm_version}-{index}",
                    f"after-{algorithm_version}-{index}",
                ),
                algorithm_version=algorithm_version,
                created_at=BASE + timedelta(hours=3, minutes=revision),
            )
        )


def _bootstrap_inventory(runtime: SQLiteRuntimeRepository) -> None:
    cutover_batch_id = insert_cutover_order_snapshot(
        runtime,
        batch_id="inventory-cutover-empty",
        observed_at=BASE - timedelta(minutes=1),
        platform_trade_date=TRADE_DATE,
        platform_name=PLATFORM,
    )
    InventoryApplicationService(runtime, clock=lambda: BASE).bootstrap(
        [
            Product(
                internal_sku="SKU-1",
                product_name="Rose",
                grade="B",
                stem_length="50",
                unit="扎",
                base_cost=Decimal("5.00"),
                current_stock=100,
                sale_enabled=True,
            )
        ],
        snapshot_sha256="sha256:" + "f" * 64,
        runtime_snapshot_sha256=sqlite_logical_snapshot_sha256(runtime),
        cutover_order_observation_batch_id=cutover_batch_id,
        idempotency_key="bootstrap:trade-day-settlement",
        actor="test",
        freeze_validator=lambda: True,
    )


def _create_sku_provisional(
    service: TradeDaySettlementService,
    *,
    platform_trade_date: date = TRADE_DATE,
):
    return service.create_provisional(
        platform_name=PLATFORM,
        platform_trade_date=platform_trade_date,
        seller_operation_date=date(2026, 8, 1),
        seller_phase=SellerPhase.NORMAL_SALES,
        time_policy_version=DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
        scope_type="SKU",
        scope_key="SKU-1",
        changed_by="test",
    )


def test_trusted_empty_cutover_applies_later_complete_sales_from_zero(
    settlement,
) -> None:
    service, _, runtime = settlement
    _bootstrap_inventory(runtime)
    inventory = InventoryRepository(runtime)
    state = inventory.get_authority_state()
    baseline = inventory.get_sales_baseline(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
        internal_sku="SKU-1",
    )
    assert state.bootstrap_sales_watermark_date == TRADE_DATE
    assert baseline is None
    assert inventory.get_balance("SKU-1").current_qty == 100
    assert inventory.get_balance("SKU-1").version == 1

    _insert_order_snapshot(
        runtime,
        "inventory-post-cutover-order",
        completed_at=BASE + timedelta(hours=2),
        rows=(("fingerprint-a", 5, "60", "SKU-1", "VERIFIED"),),
    )
    _create_sku_provisional(service)
    applied = InventorySalesApplicationService(
        runtime,
        clock=lambda: BASE,
    ).apply_current_sku_summaries(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
    )
    assert applied.applied_sku_count == 1
    assert inventory.get_balance("SKU-1").current_qty == 95


def test_trusted_empty_cutover_does_not_seed_conflicting_current_estimate(
    settlement,
) -> None:
    service, repository, runtime = settlement
    _append_complete_estimate_day(repository, sold_qty=5)
    _create_sku_provisional(service)
    _bootstrap_inventory(runtime)

    _insert_order_snapshot(
        runtime,
        "inventory-cutover-order-replacement",
        completed_at=BASE + timedelta(hours=3),
        rows=(("fingerprint-a", 3, "36", "SKU-1", "VERIFIED"),),
    )
    _create_sku_provisional(service)
    result = InventorySalesApplicationService(
        runtime,
        clock=lambda: BASE,
    ).apply_current_sku_summaries(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
    )

    assert result.applied_sku_count == 1
    assert InventoryRepository(runtime).get_balance("SKU-1").current_qty == 97


def test_historical_order_backfill_after_cutover_updates_baseline_only(
    settlement,
) -> None:
    service, _, runtime = settlement
    _bootstrap_inventory(runtime)
    historical_date = TRADE_DATE - timedelta(days=1)
    _insert_order_snapshot(
        runtime,
        "inventory-historical-backfill",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 4, "48", "SKU-1", "VERIFIED"),),
        platform_trade_date=historical_date,
    )
    _create_sku_provisional(service, platform_trade_date=historical_date)

    result = InventorySalesApplicationService(
        runtime,
        clock=lambda: BASE,
    ).apply_current_sku_summaries(
        platform_name=PLATFORM,
        platform_trade_date=historical_date,
    )
    inventory = InventoryRepository(runtime)
    baseline = inventory.get_sales_baseline(
        platform_name=PLATFORM,
        platform_trade_date=historical_date,
        internal_sku="SKU-1",
    )

    assert result.applied_sku_count == 1
    assert inventory.get_balance("SKU-1").current_qty == 100
    assert inventory.get_balance("SKU-1").version == 1
    assert baseline is not None and baseline.selected_sold_qty == 4
    assert inventory.list_transactions(internal_sku="SKU-1")[0].transaction_type == (
        "SALES_BASELINE_SYNC"
    )


def test_inventory_applies_latest_complete_order_net_difference_and_cancel_restore(
    settlement,
) -> None:
    service, _, runtime = settlement
    _bootstrap_inventory(runtime)
    inventory_sales = InventorySalesApplicationService(runtime, clock=lambda: BASE)
    _insert_order_snapshot(
        runtime,
        "inventory-order-1",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 3, "36", "SKU-1", "VERIFIED"),),
    )
    _create_sku_provisional(service)

    first = inventory_sales.apply_current_sku_summaries(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
    )
    assert first.applied_sku_count == 1
    assert InventoryRepository(runtime).get_balance("SKU-1").current_qty == 97

    _insert_order_snapshot(
        runtime,
        "inventory-order-2",
        completed_at=BASE + timedelta(hours=2),
        rows=(("fingerprint-a", 1, "12", "SKU-1", "VERIFIED"),),
    )
    _create_sku_provisional(service)
    second = inventory_sales.apply_current_sku_summaries(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
    )

    assert second.applied_sku_count == 1
    assert InventoryRepository(runtime).get_balance("SKU-1").current_qty == 99
    transactions = InventoryRepository(runtime).list_transactions(
        internal_sku="SKU-1"
    )
    assert {item.transaction_type for item in transactions[:2]} == {
        "SALES_RESTORE",
        "SALES_DEDUCTION",
    }


def test_complete_order_replaces_applied_estimate_using_only_net_difference(
    settlement,
) -> None:
    service, repository, runtime = settlement
    _bootstrap_inventory(runtime)
    inventory_sales = InventorySalesApplicationService(runtime, clock=lambda: BASE)
    estimate_date = TRADE_DATE + timedelta(days=1)
    _append_complete_estimate_day(
        repository,
        sold_qty=5,
        platform_trade_date=estimate_date,
    )
    _create_sku_provisional(service, platform_trade_date=estimate_date)
    inventory_sales.apply_current_sku_summaries(
        platform_name=PLATFORM,
        platform_trade_date=estimate_date,
    )
    assert InventoryRepository(runtime).get_balance("SKU-1").current_qty == 95

    _insert_order_snapshot(
        runtime,
        "inventory-order-replacement",
        completed_at=BASE + timedelta(hours=4),
        rows=(("fingerprint-a", 3, "36", "SKU-1", "VERIFIED"),),
        platform_trade_date=estimate_date,
    )
    _create_sku_provisional(service, platform_trade_date=estimate_date)
    result = inventory_sales.apply_current_sku_summaries(
        platform_name=PLATFORM,
        platform_trade_date=estimate_date,
    )

    assert result.applied_sku_count == 1
    assert InventoryRepository(runtime).get_balance("SKU-1").current_qty == 97


def test_partial_or_open_order_summary_never_changes_inventory(
    settlement,
) -> None:
    service, _, runtime = settlement
    _bootstrap_inventory(runtime)
    _insert_order_snapshot(
        runtime,
        "inventory-order-open",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 3, "36", "SKU-1", "VERIFIED"),),
        trade_day_status="OPEN",
    )
    _create_sku_provisional(service)

    result = InventorySalesApplicationService(
        runtime,
        clock=lambda: BASE,
    ).apply_current_sku_summaries(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
    )

    assert result.applied_sku_count == 0
    assert result.skipped_sku_count == 1
    assert InventoryRepository(runtime).get_balance("SKU-1").current_qty == 100


def test_estimate_decrease_never_restores_inventory_or_moves_baseline(
    settlement,
) -> None:
    service, repository, runtime = settlement
    _bootstrap_inventory(runtime)
    inventory_sales = InventorySalesApplicationService(runtime, clock=lambda: BASE)
    estimate_date = TRADE_DATE + timedelta(days=1)
    _append_complete_estimate_day(
        repository,
        sold_qty=5,
        platform_trade_date=estimate_date,
    )
    _create_sku_provisional(service, platform_trade_date=estimate_date)
    inventory_sales.apply_current_sku_summaries(
        platform_name=PLATFORM,
        platform_trade_date=estimate_date,
    )

    _append_complete_estimate_day(
        repository,
        sold_qty=3,
        platform_trade_date=estimate_date,
        revision=1,
    )
    _create_sku_provisional(service, platform_trade_date=estimate_date)
    result = inventory_sales.apply_current_sku_summaries(
        platform_name=PLATFORM,
        platform_trade_date=estimate_date,
    )
    baseline = InventoryRepository(runtime).get_sales_baseline(
        platform_name=PLATFORM,
        platform_trade_date=estimate_date,
        internal_sku="SKU-1",
    )

    assert result.applied_sku_count == 0
    assert result.skipped_sku_count == 1
    assert InventoryRepository(runtime).get_balance("SKU-1").current_qty == 95
    assert baseline is not None and baseline.selected_sold_qty == 5


@pytest.mark.parametrize(
    "quality_level",
    [
        DataQualityLevel.SCAN_ESTIMATED_MEDIUM,
        DataQualityLevel.SCAN_ESTIMATED_LOW,
    ],
)
def test_medium_or_low_scan_evidence_never_changes_inventory(
    settlement,
    quality_level: DataQualityLevel,
) -> None:
    service, repository, runtime = settlement
    _bootstrap_inventory(runtime)
    _append_complete_estimate_day(
        repository,
        sold_qty=4,
        quality_level=quality_level,
    )
    _create_sku_provisional(service)

    result = InventorySalesApplicationService(
        runtime,
        clock=lambda: BASE,
    ).apply_current_sku_summaries(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
    )

    assert result.applied_sku_count == 0
    assert result.skipped_sku_count == 1
    assert InventoryRepository(runtime).get_balance("SKU-1").current_qty == 100


def test_unavailable_or_other_trade_day_never_changes_inventory(
    settlement,
) -> None:
    service, _, runtime = settlement
    _bootstrap_inventory(runtime)
    _create_sku_provisional(service)
    inventory_sales = InventorySalesApplicationService(runtime, clock=lambda: BASE)

    unavailable = inventory_sales.apply_current_sku_summaries(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
    )
    other_day = inventory_sales.apply_current_sku_summaries(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE + timedelta(days=1),
    )

    assert unavailable.applied_sku_count == 0
    assert unavailable.skipped_sku_count == 1
    assert other_day.applied_sku_count == 0
    assert InventoryRepository(runtime).get_balance("SKU-1").current_qty == 100


def test_sales_inventory_database_failure_rolls_back_ledger_and_baseline(
    settlement,
) -> None:
    service, _, runtime = settlement
    _bootstrap_inventory(runtime)
    _insert_order_snapshot(
        runtime,
        "inventory-order-rollback",
        completed_at=BASE + timedelta(hours=1),
        rows=(("fingerprint-a", 3, "36", "SKU-1", "VERIFIED"),),
    )
    _create_sku_provisional(service)
    with closing(runtime.connect_write()) as connection, connection:
        connection.execute(
            """
            CREATE TRIGGER fail_inventory_balance_update
            BEFORE UPDATE ON inventory_balances
            BEGIN
                SELECT RAISE(ABORT, 'injected inventory failure');
            END
            """
        )

    with pytest.raises(Exception, match="injected inventory failure"):
        InventorySalesApplicationService(
            runtime,
            clock=lambda: BASE,
        ).apply_current_sku_summaries(
            platform_name=PLATFORM,
            platform_trade_date=TRADE_DATE,
        )

    inventory = InventoryRepository(runtime)
    assert inventory.get_balance("SKU-1").current_qty == 100
    assert inventory.get_sales_baseline(
        platform_name=PLATFORM,
        platform_trade_date=TRADE_DATE,
        internal_sku="SKU-1",
    ) is None
    assert len(inventory.list_transactions(internal_sku="SKU-1")) == 1


def hashlib_for(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()

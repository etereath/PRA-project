from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.enums import (
    DataQualityLevel,
    FactSource,
    ProductMappingStatus,
    SellerPhase,
    SummaryStatus,
)
from app.operational_models import PlatformTradeDaySummary
from app.sales_settlement_models import (
    InventoryObservationPoint,
    OrderSnapshot,
    OrderSnapshotItem,
)
from app.services.sales_plan_input import SalesPlanInputService
from app.services.settlement_pipeline import (
    build_management_report,
    validate_settlement_event_payload_size,
)


PLATFORM = "platform"
SETTLED_DATE = date(2026, 8, 1)
PLAN_DATE = date(2026, 8, 2)
AS_OF = datetime(2026, 8, 1, 12, 5, tzinfo=timezone.utc)


class FakePlanRepository:
    def __init__(self, *, summaries=(), orders=(), inventory=()) -> None:
        self.summaries = tuple(summaries)
        self.orders = tuple(orders)
        self.inventory = tuple(inventory)

    def list_current_summaries(self, **_kwargs):
        return self.summaries

    def list_order_snapshots(self, **_kwargs):
        return self.orders

    def list_inventory_observations_for_seller_operation_date(self, **_kwargs):
        return self.inventory


def _summary(
    *,
    quality: DataQualityLevel = DataQualityLevel.ORDER_COMPLETE,
    sold_qty: int | None = 5,
    order_count: int | None = 2,
    amount: Decimal | None = Decimal("60"),
    scope_type: str = "PLATFORM",
    scope_key: str = PLATFORM,
) -> PlatformTradeDaySummary:
    fact_source = (
        None
        if quality is DataQualityLevel.UNAVAILABLE
        else (
            FactSource.ORDER_OBSERVED
            if quality
            in {DataQualityLevel.ORDER_COMPLETE, DataQualityLevel.ORDER_PARTIAL}
            else FactSource.SCAN_ESTIMATED
        )
    )
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    return PlatformTradeDaySummary(
        summary_id=f"summary-{scope_type}-{scope_key}",
        summary_series_id=f"series-{scope_type}-{scope_key}",
        version_no=1,
        supersedes_summary_id=None,
        is_current=True,
        platform_name=PLATFORM,
        platform_trade_date=SETTLED_DATE,
        seller_operation_date=PLAN_DATE,
        seller_phase=SellerPhase.NORMAL_SALES,
        scope_type=scope_type,
        scope_key=scope_key,
        fact_source=fact_source,
        quality_level=quality,
        summary_status=SummaryStatus.PROVISIONAL,
        sold_qty=sold_qty,
        order_count=order_count,
        transaction_amount_total=amount,
        quality_reason="test",
        source_proportions={},
        input_manifest_sha256="sha256:" + "a" * 64,
        mapping_version="mapping-v1",
        algorithm_version="settlement-v1",
        time_policy_version="CN_SINGLE_PLATFORM_2026_V1",
        finalized_at=None,
        created_at=now,
        updated_at=now,
    )


def _order_item(
    suffix: str,
    *,
    created_at: datetime,
    qty: int,
    amount: str,
) -> OrderSnapshotItem:
    return OrderSnapshotItem(
        observation_item_id=f"item-{suffix}",
        order_identity_fingerprint=f"fingerprint-{suffix}",
        occurrence_no=1,
        order_created_at=created_at,
        platform_product_name="Rose",
        grade="B",
        internal_sku="SKU-1",
        mapping_status=ProductMappingStatus.VERIFIED,
        mapping_version="mapping-v1",
        order_qty=qty,
        order_transaction_amount=Decimal(amount),
        raw_observation_sha256="b" * 64,
    )


def _order_snapshot(
    batch_id: str,
    *,
    completed_at: datetime,
    items=(),
    capability_result: str = "SUCCEEDED",
    source_batch_status: str = "ACCEPTED",
    scope_complete: bool | None = None,
    end_marker_verified: bool | None = None,
) -> OrderSnapshot:
    accepted = capability_result == "SUCCEEDED" and source_batch_status == "ACCEPTED"
    return OrderSnapshot(
        observation_batch_id=batch_id,
        platform_name=PLATFORM,
        platform_trade_date=PLAN_DATE,
        trade_day_status="OPEN",
        capability_result=capability_result,
        batch_status="ACCEPTED" if accepted else "FAILED",
        source_batch_status=source_batch_status,
        scope_complete=(accepted if scope_complete is None else scope_complete),
        end_marker_verified=(
            accepted
            if end_marker_verified is None
            else end_marker_verified
        ),
        scan_started_at=completed_at - timedelta(minutes=1),
        scan_completed_at=completed_at,
        content_sha256="sha256:" + batch_id[-1] * 64,
        time_policy_version="CN_SINGLE_PLATFORM_2026_V1",
        mapping_version="mapping-v1",
        items=tuple(items),
    )


def _inventory(
    name: str,
    *,
    observed_at: datetime,
    price: str,
    inventory: int,
    batch_status: str = "ACCEPTED",
) -> InventoryObservationPoint:
    return InventoryObservationPoint(
        observation_item_id=name,
        observation_batch_id=f"batch-{name}",
        platform_name=PLATFORM,
        internal_sku="SKU-1",
        platform_trade_date=PLAN_DATE,
        observed_at=observed_at,
        observed_price=Decimal(price),
        observed_inventory=inventory,
        observed_online=True,
        mapping_status=ProductMappingStatus.VERIFIED,
        mapping_version="mapping-v1",
        scan_type="ONLINE_PULSE",
        batch_status=batch_status,
        scope_complete=batch_status == "ACCEPTED",
        end_marker_verified=batch_status == "ACCEPTED",
        content_sha256="sha256:" + name[-1] * 64,
    )


def test_plan_requires_platform_settlement() -> None:
    service = SalesPlanInputService(FakePlanRepository())
    with pytest.raises(ValueError, match="PLATFORM summary"):
        service.build(
            platform_name=PLATFORM,
            settled_platform_trade_date=SETTLED_DATE,
            plan_for_seller_operation_date=PLAN_DATE,
            as_of=AS_OF,
        )


def test_plan_filters_early_orders_by_occurrence_time_and_ignores_newer_failure() -> None:
    accepted = _order_snapshot(
        "batch-a",
        completed_at=AS_OF,
        items=(
            _order_item(
                "early",
                created_at=datetime(2026, 8, 1, 10, 30, tzinfo=timezone.utc),
                qty=2,
                amount="24",
            ),
            _order_item(
                "late",
                created_at=datetime(2026, 8, 1, 12, 1, tzinfo=timezone.utc),
                qty=9,
                amount="108",
            ),
        ),
    )
    failed = _order_snapshot(
        "batch-f",
        completed_at=AS_OF + timedelta(minutes=1),
        capability_result="FAILED",
        source_batch_status="FAILED",
    )
    inventory = (
        _inventory(
            "point-1",
            observed_at=datetime(2026, 8, 1, 12, 10, tzinfo=timezone.utc),
            price="12",
            inventory=20,
        ),
        _inventory(
            "point-2",
            observed_at=datetime(2026, 8, 1, 12, 20, tzinfo=timezone.utc),
            price="13",
            inventory=18,
        ),
        _inventory(
            "point-3",
            observed_at=datetime(2026, 8, 1, 12, 30, tzinfo=timezone.utc),
            price="99",
            inventory=1,
            batch_status="PARTIAL",
        ),
    )
    manifest = SalesPlanInputService(
        FakePlanRepository(
            summaries=(_summary(),),
            orders=(accepted, failed),
            inventory=inventory,
        )
    ).build(
        platform_name=PLATFORM,
        settled_platform_trade_date=SETTLED_DATE,
        plan_for_seller_operation_date=PLAN_DATE,
        as_of=AS_OF,
    )

    early = manifest.payload["pre_plan_early_signal"]
    assert early["quality_state"] == "CONFIRMED"
    assert early["order_count"] == 1
    assert early["sold_qty"] == 2
    assert early["transaction_amount_total"] == "24"
    assert early["trusted_zero"] is False
    trajectory = manifest.payload["seller_operation_inventory_trajectory"]
    assert trajectory == [
        {
            "internal_sku": "SKU-1",
            "observation_count": 2,
            "first_observed_at": "2026-08-01T12:10:00+00:00",
            "last_observed_at": "2026-08-01T12:20:00+00:00",
            "opening_price": "12",
            "closing_price": "13",
            "minimum_price": "12",
            "maximum_price": "13",
            "price_change_count": 1,
            "opening_inventory": 20,
            "closing_inventory": 18,
            "minimum_inventory": 18,
            "maximum_inventory": 20,
            "online_observation_count": 2,
            "mapping_version": "mapping-v1",
            "quality": "ACCEPTED_COMPLETE",
        }
    ]
    assert manifest.projection_role == "CURRENT_OPERATIONS"
    assert manifest.payload["plan_input_status"] == "ELIGIBLE"


def test_trusted_empty_early_snapshot_is_confirmed_zero() -> None:
    empty = _order_snapshot("batch-e", completed_at=AS_OF, items=())
    manifest = SalesPlanInputService(
        FakePlanRepository(summaries=(_summary(),), orders=(empty,))
    ).build(
        platform_name=PLATFORM,
        settled_platform_trade_date=SETTLED_DATE,
        plan_for_seller_operation_date=PLAN_DATE,
        as_of=AS_OF,
    )

    early = manifest.payload["pre_plan_early_signal"]
    assert early["trusted_zero"] is True
    assert early["order_count"] == 0
    assert early["sold_qty"] == 0
    assert early["transaction_amount_total"] == "0"


def test_stale_empty_snapshot_and_later_failure_are_not_trusted() -> None:
    stale_empty = _order_snapshot(
        "batch-s",
        completed_at=datetime(2026, 8, 1, 10, 10, tzinfo=timezone.utc),
        items=(),
    )
    later_failure = _order_snapshot(
        "batch-f",
        completed_at=datetime(2026, 8, 1, 11, 50, tzinfo=timezone.utc),
        capability_result="FAILED",
        source_batch_status="FAILED",
    )

    manifest = SalesPlanInputService(
        FakePlanRepository(
            summaries=(_summary(),),
            orders=(stale_empty, later_failure),
        )
    ).build(
        platform_name=PLATFORM,
        settled_platform_trade_date=SETTLED_DATE,
        plan_for_seller_operation_date=PLAN_DATE,
        as_of=AS_OF,
    )

    early = manifest.payload["pre_plan_early_signal"]
    assert early["quality_state"] == "EVIDENCE_INSUFFICIENT"
    assert early["trusted_zero"] is False
    assert early["sold_qty"] is None
    assert early["reason"] == "LATER_ORDER_SCAN_FAILED_OR_INCOMPLETE"
    assert {
        ref_id
        for input_type, ref_id, _ in manifest.input_refs
        if input_type == "EARLY_ORDER_OBSERVATION_BATCH"
    } == {"batch-s", "batch-f"}


def test_stale_empty_snapshot_without_boundary_coverage_is_not_trusted() -> None:
    stale_empty = _order_snapshot(
        "batch-s",
        completed_at=datetime(2026, 8, 1, 10, 10, tzinfo=timezone.utc),
        items=(),
    )
    manifest = SalesPlanInputService(
        FakePlanRepository(summaries=(_summary(),), orders=(stale_empty,))
    ).build(
        platform_name=PLATFORM,
        settled_platform_trade_date=SETTLED_DATE,
        plan_for_seller_operation_date=PLAN_DATE,
        as_of=AS_OF,
    )

    early = manifest.payload["pre_plan_early_signal"]
    assert early["quality_state"] == "EVIDENCE_INSUFFICIENT"
    assert early["trusted_zero"] is False
    assert early["reason"] == "ORDER_SNAPSHOT_STALE_AT_PLAN_BOUNDARY"


@pytest.mark.parametrize(
    "items",
    (
        (),
        (
            _order_item(
                "pre-boundary",
                created_at=datetime(2026, 8, 1, 11, 30, tzinfo=timezone.utc),
                qty=2,
                amount="24",
            ),
        ),
    ),
)
def test_pre_boundary_complete_snapshot_cannot_confirm_full_window(items) -> None:
    snapshot = _order_snapshot(
        "batch-p",
        completed_at=datetime(2026, 8, 1, 11, 55, tzinfo=timezone.utc),
        items=items,
    )
    manifest = SalesPlanInputService(
        FakePlanRepository(summaries=(_summary(),), orders=(snapshot,))
    ).build(
        platform_name=PLATFORM,
        settled_platform_trade_date=SETTLED_DATE,
        plan_for_seller_operation_date=PLAN_DATE,
        as_of=AS_OF,
    )

    early = manifest.payload["pre_plan_early_signal"]
    assert early["quality_state"] == "EVIDENCE_INSUFFICIENT"
    assert early["trusted_zero"] is False
    assert early["order_count"] is None
    assert early["sold_qty"] is None
    assert early["transaction_amount_total"] is None
    assert early["reason"] == "ORDER_SNAPSHOT_STALE_AT_PLAN_BOUNDARY"


@pytest.mark.parametrize("minute", (1, 10))
def test_post_boundary_complete_empty_snapshot_can_confirm_zero(
    minute: int,
) -> None:
    snapshot = _order_snapshot(
        f"batch-{minute}",
        completed_at=datetime(2026, 8, 1, 12, minute, tzinfo=timezone.utc),
        items=(),
    )
    as_of = max(AS_OF, snapshot.scan_completed_at)
    manifest = SalesPlanInputService(
        FakePlanRepository(summaries=(_summary(),), orders=(snapshot,))
    ).build(
        platform_name=PLATFORM,
        settled_platform_trade_date=SETTLED_DATE,
        plan_for_seller_operation_date=PLAN_DATE,
        as_of=as_of,
    )

    early = manifest.payload["pre_plan_early_signal"]
    assert early["quality_state"] == "CONFIRMED"
    assert early["trusted_zero"] is True
    assert early["order_count"] == 0
    assert early["sold_qty"] == 0
    assert early["transaction_amount_total"] == "0"


@pytest.mark.parametrize(
    "later",
    (
        _order_snapshot(
            "batch-f",
            completed_at=datetime(2026, 8, 1, 12, 4, tzinfo=timezone.utc),
            capability_result="FAILED",
            source_batch_status="FAILED",
        ),
        _order_snapshot(
            "batch-i",
            completed_at=datetime(2026, 8, 1, 12, 4, tzinfo=timezone.utc),
            scope_complete=False,
        ),
    ),
)
def test_post_boundary_complete_snapshot_is_blocked_by_later_failure(
    later: OrderSnapshot,
) -> None:
    complete = _order_snapshot(
        "batch-c",
        completed_at=datetime(2026, 8, 1, 12, 1, tzinfo=timezone.utc),
        items=(),
    )
    manifest = SalesPlanInputService(
        FakePlanRepository(
            summaries=(_summary(),),
            orders=(complete, later),
        )
    ).build(
        platform_name=PLATFORM,
        settled_platform_trade_date=SETTLED_DATE,
        plan_for_seller_operation_date=PLAN_DATE,
        as_of=AS_OF,
    )

    early = manifest.payload["pre_plan_early_signal"]
    assert early["quality_state"] == "EVIDENCE_INSUFFICIENT"
    assert early["trusted_zero"] is False
    assert early["sold_qty"] is None
    assert early["reason"] == "LATER_ORDER_SCAN_FAILED_OR_INCOMPLETE"


def test_latest_fresh_complete_snapshot_wins() -> None:
    older = _order_snapshot(
        "batch-o",
        completed_at=datetime(2026, 8, 1, 11, 52, tzinfo=timezone.utc),
        items=(
            _order_item(
                "older",
                created_at=datetime(2026, 8, 1, 10, 30, tzinfo=timezone.utc),
                qty=1,
                amount="12",
            ),
        ),
    )
    newer = _order_snapshot(
        "batch-n",
        completed_at=datetime(2026, 8, 1, 12, 1, tzinfo=timezone.utc),
        items=(
            _order_item(
                "newer",
                created_at=datetime(2026, 8, 1, 11, 30, tzinfo=timezone.utc),
                qty=2,
                amount="24",
            ),
        ),
    )
    manifest = SalesPlanInputService(
        FakePlanRepository(
            summaries=(_summary(),),
            orders=(older, newer),
        )
    ).build(
        platform_name=PLATFORM,
        settled_platform_trade_date=SETTLED_DATE,
        plan_for_seller_operation_date=PLAN_DATE,
        as_of=AS_OF,
    )

    early = manifest.payload["pre_plan_early_signal"]
    assert early["quality_state"] == "CONFIRMED"
    assert early["observation_batch_id"] == "batch-n"
    assert early["sold_qty"] == 2


def test_low_quality_and_historical_plan_are_not_executable() -> None:
    partial = _summary(
        quality=DataQualityLevel.ORDER_PARTIAL,
        sold_qty=3,
        order_count=1,
        amount=Decimal("36"),
    )
    current = SalesPlanInputService(
        FakePlanRepository(summaries=(partial,))
    ).build(
        platform_name=PLATFORM,
        settled_platform_trade_date=SETTLED_DATE,
        plan_for_seller_operation_date=PLAN_DATE,
        as_of=AS_OF,
    )
    historical = SalesPlanInputService(
        FakePlanRepository(
            summaries=(_summary(),),
            orders=(
                _order_snapshot("batch-h", completed_at=AS_OF, items=()),
            ),
        )
    ).build(
        platform_name=PLATFORM,
        settled_platform_trade_date=SETTLED_DATE,
        plan_for_seller_operation_date=PLAN_DATE - timedelta(days=1),
        as_of=AS_OF,
    )

    assert current.payload["plan_input_status"] == "INELIGIBLE"
    assert current.payload["closed_trade_day_summaries"][0]["sold_qty"] is None
    assert historical.projection_role == "AUDIT_ONLY"
    assert historical.payload["plan_input_status"] == "INELIGIBLE"
    assert "HISTORICAL_AUDIT_ONLY" in historical.payload["ineligibility_reasons"]
    historical_early = historical.payload["pre_plan_early_signal"]
    assert historical_early["projection_role"] == "AUDIT_ONLY"
    assert historical_early["operational_use_allowed"] is False


@pytest.mark.parametrize(
    ("summary", "state"),
    [
        (_summary(sold_qty=0, order_count=0, amount=Decimal("0")), "CONFIRMED_ZERO"),
        (
            _summary(
                quality=DataQualityLevel.ORDER_PARTIAL,
                sold_qty=1,
                order_count=1,
                amount=Decimal("12"),
            ),
            "EVIDENCE_INSUFFICIENT",
        ),
        (
            _summary(
                quality=DataQualityLevel.UNAVAILABLE,
                sold_qty=None,
                order_count=None,
                amount=None,
            ),
            "NO_DATA_OR_UNREADABLE",
        ),
    ],
)
def test_management_report_distinguishes_zero_insufficient_and_unavailable(
    summary,
    state,
) -> None:
    report = build_management_report(
        summaries=(summary,),
        cancellation=None,
        seller_operation_date=PLAN_DATE,
    )

    assert report["sales"]["state"] == state
    if state == "CONFIRMED_ZERO":
        assert report["sales"]["message"] == "当天暂无成交"
        assert report["top_varieties"] == {
            "state": "CONFIRMED_ZERO",
            "items": [],
        }


def test_automation_event_payload_is_size_bounded() -> None:
    with pytest.raises(ValueError, match="bounded Automation Event"):
        validate_settlement_event_payload_size({"value": "x" * (70 * 1024)})

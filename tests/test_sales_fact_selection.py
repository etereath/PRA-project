from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.enums import DataQualityLevel, ProductMappingStatus
from app.sales_settlement_models import (
    OrderSnapshot,
    OrderSnapshotItem,
    SalesEstimateSegment,
)
from app.services.sales_fact_selection import SalesFactSelectionService


TRADE_DATE = date(2026, 7, 31)
NOW = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)


def _order_item() -> OrderSnapshotItem:
    return OrderSnapshotItem(
        observation_item_id="item-1",
        order_identity_fingerprint="fingerprint-1",
        occurrence_no=1,
        order_created_at=NOW,
        platform_product_name="Rose",
        grade="B",
        internal_sku="SKU-1",
        mapping_status=ProductMappingStatus.VERIFIED,
        mapping_version="mapping-v1",
        order_qty=3,
        order_transaction_amount=Decimal("36"),
        raw_observation_sha256="a" * 64,
    )


def _snapshot(
    *,
    trade_day_status: str = "CLOSED",
    source_batch_status: str = "ACCEPTED",
) -> OrderSnapshot:
    return OrderSnapshot(
        observation_batch_id="batch-1",
        platform_name="platform",
        platform_trade_date=TRADE_DATE,
        trade_day_status=trade_day_status,
        capability_result="SUCCEEDED",
        batch_status=(
            "ACCEPTED" if source_batch_status == "ACCEPTED" else "PARTIAL"
        ),
        source_batch_status=source_batch_status,
        scope_complete=source_batch_status == "ACCEPTED",
        end_marker_verified=source_batch_status == "ACCEPTED",
        scan_started_at=NOW,
        scan_completed_at=NOW + timedelta(minutes=1),
        content_sha256="sha256:" + "b" * 64,
        time_policy_version="policy-v1",
        mapping_version="mapping-v1",
        items=(_order_item(),),
    )


def _estimate() -> SalesEstimateSegment:
    return SalesEstimateSegment(
        estimate_segment_id="segment-1",
        platform_name="platform",
        internal_sku="SKU-1",
        platform_trade_date=TRADE_DATE,
        interval_started_at=NOW,
        interval_ended_at=NOW + timedelta(minutes=10),
        inventory_before=20,
        inventory_after=15,
        known_inventory_adjustment=0,
        known_adjustment_source_refs=(),
        estimated_sold_qty=5,
        estimation_eligible=True,
        estimation_reason="ELIGIBLE_NO_ADJUSTMENT",
        quality_level=DataQualityLevel.SCAN_ESTIMATED_HIGH,
        mapping_version="mapping-v1",
        supporting_observation_ids=("before", "after"),
        algorithm_version="estimate-v1",
        created_at=NOW,
    )


def _select(*, orders=(), estimates=()):
    return SalesFactSelectionService().select(
        platform_name="platform",
        platform_trade_date=TRADE_DATE,
        scope_type="PLATFORM",
        scope_key="platform",
        order_snapshots=orders,
        estimate_segments=estimates,
    )


def test_complete_order_replaces_estimate_instead_of_adding_it() -> None:
    selected = _select(orders=(_snapshot(),), estimates=(_estimate(),))
    assert selected.quality_level is DataQualityLevel.ORDER_COMPLETE
    assert selected.sold_qty == 3
    assert selected.sold_qty != 8
    assert {
        input_type for input_type, _, _ in selected.input_refs
    } == {"ORDER_OBSERVATION_BATCH"}


def test_partial_order_is_kept_separate_and_not_filled_with_estimate() -> None:
    selected = _select(
        orders=(_snapshot(source_batch_status="PARTIAL"),),
        estimates=(_estimate(),),
    )
    assert selected.quality_level is DataQualityLevel.ORDER_PARTIAL
    assert selected.sold_qty == 3
    assert selected.sold_qty != 8


def test_open_order_is_partial_and_still_has_priority_over_estimate() -> None:
    selected = _select(
        orders=(_snapshot(trade_day_status="OPEN"),),
        estimates=(_estimate(),),
    )
    assert selected.quality_level is DataQualityLevel.ORDER_PARTIAL
    assert selected.sold_qty == 3


def test_estimate_is_used_only_when_no_acceptable_order_exists() -> None:
    selected = _select(estimates=(_estimate(),))
    assert selected.quality_level is DataQualityLevel.SCAN_ESTIMATED_HIGH
    assert selected.sold_qty == 5
    assert selected.order_count is None
    assert selected.transaction_amount_total is None

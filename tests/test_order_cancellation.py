from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.enums import ProductMappingStatus
from app.sales_settlement_models import OrderSnapshot, OrderSnapshotItem
from app.services.order_cancellation import OrderCancellationService


TRADE_DATE = date(2026, 7, 31)
START = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)


def _item(
    name: str,
    *,
    fingerprint: str,
    occurrence_no: int,
    qty: int,
    amount: str,
    mapping_status: ProductMappingStatus = ProductMappingStatus.VERIFIED,
) -> OrderSnapshotItem:
    return OrderSnapshotItem(
        observation_item_id=name,
        order_identity_fingerprint=fingerprint,
        occurrence_no=occurrence_no,
        order_created_at=START,
        platform_product_name="Rose",
        grade="B",
        internal_sku="SKU-1",
        mapping_status=mapping_status,
        mapping_version="mapping-v1",
        order_qty=qty,
        order_transaction_amount=Decimal(amount),
        raw_observation_sha256="a" * 64,
    )


def _snapshot(
    name: str,
    *,
    minute: int,
    items: tuple[OrderSnapshotItem, ...],
    trade_day_status: str = "CLOSED",
) -> OrderSnapshot:
    return OrderSnapshot(
        observation_batch_id=name,
        platform_name="platform",
        platform_trade_date=TRADE_DATE,
        trade_day_status=trade_day_status,
        capability_result="SUCCEEDED",
        batch_status="ACCEPTED",
        source_batch_status="ACCEPTED",
        scope_complete=True,
        end_marker_verified=True,
        scan_started_at=START + timedelta(minutes=minute - 1),
        scan_completed_at=START + timedelta(minutes=minute),
        content_sha256="sha256:" + name[-1] * 64,
        time_policy_version="policy-v1",
        mapping_version="mapping-v1",
        items=items,
    )


def test_duplicate_fingerprint_decrease_preserves_occurrence_quantity() -> None:
    previous = _snapshot(
        "batch-a",
        minute=1,
        items=(
            _item(
                "a1",
                fingerprint="same",
                occurrence_no=1,
                qty=1,
                amount="12",
            ),
            _item(
                "a2",
                fingerprint="same",
                occurrence_no=2,
                qty=2,
                amount="24",
            ),
        ),
    )
    current = _snapshot(
        "batch-b",
        minute=2,
        items=(
            _item(
                "b1",
                fingerprint="same",
                occurrence_no=1,
                qty=1,
                amount="12.00",
            ),
        ),
    )

    result = OrderCancellationService().compare(previous, current)

    assert result.status == "DETERMINED"
    assert result.cancelled_order_count == 1
    assert result.cancelled_qty == 2


def test_same_identity_content_change_is_ambiguous() -> None:
    previous = _snapshot(
        "batch-a",
        minute=1,
        items=(
            _item(
                "a1", fingerprint="same", occurrence_no=1, qty=1, amount="12"
            ),
            _item(
                "a2", fingerprint="same", occurrence_no=2, qty=2, amount="24"
            ),
        ),
    )
    current = _snapshot(
        "batch-b",
        minute=2,
        items=(
            _item(
                "b1", fingerprint="same", occurrence_no=1, qty=3, amount="36"
            ),
        ),
    )

    result = OrderCancellationService().compare(previous, current)

    assert result.status == "CANCELLATION_AMBIGUOUS"
    assert result.cancelled_qty is None


def test_open_or_mapping_failed_snapshot_is_not_eligible() -> None:
    previous = _snapshot("batch-a", minute=1, items=())
    current_open = _snapshot(
        "batch-b",
        minute=2,
        items=(),
        trade_day_status="OPEN",
    )
    open_result = OrderCancellationService().compare(previous, current_open)
    assert open_result.status == "NOT_ELIGIBLE"
    assert open_result.reason == "OPEN_TRADE_DAY"

    current_unmapped = _snapshot(
        "batch-c",
        minute=3,
        items=(
            _item(
                "c1",
                fingerprint="same",
                occurrence_no=1,
                qty=1,
                amount="12",
                mapping_status=ProductMappingStatus.UNMAPPED,
            ),
        ),
    )
    mapping_result = OrderCancellationService().compare(
        previous,
        current_unmapped,
    )
    assert mapping_result.status == "NOT_ELIGIBLE"
    assert mapping_result.reason == "MAPPING_NOT_VERIFIED"


def test_new_identity_does_not_hide_or_inflate_cancellation() -> None:
    previous = _snapshot(
        "batch-a",
        minute=1,
        items=(
            _item(
                "a1", fingerprint="old", occurrence_no=1, qty=2, amount="24"
            ),
        ),
    )
    current = _snapshot(
        "batch-b",
        minute=2,
        items=(
            _item(
                "b1", fingerprint="new", occurrence_no=1, qty=4, amount="48"
            ),
        ),
    )
    result = OrderCancellationService().compare(previous, current)
    assert result.status == "DETERMINED"
    assert result.cancelled_qty == 2
    assert result.cancelled_order_count == 1

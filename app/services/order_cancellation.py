from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal

from app.enums import ProductMappingStatus
from app.sales_settlement_models import (
    OrderCancellationResult,
    OrderSnapshot,
    OrderSnapshotItem,
)


ORDER_CANCELLATION_ALGORITHM_VERSION = "order-cancellation-multiset-v1"


class OrderCancellationService:
    """Derive cancellations without mutating immutable order observations."""

    def compare(
        self,
        previous: OrderSnapshot,
        current: OrderSnapshot,
        *,
        are_adjacent_complete_snapshots: bool = True,
    ) -> OrderCancellationResult:
        reason = _comparison_ineligibility_reason(
            previous,
            current,
            are_adjacent_complete_snapshots=are_adjacent_complete_snapshots,
        )
        if reason:
            return _result(
                status="NOT_ELIGIBLE",
                previous=previous,
                current=current,
                cancelled_order_count=None,
                cancelled_qty=None,
                reason=reason,
            )

        previous_by_identity = _group_by_identity(previous.items)
        current_by_identity = _group_by_identity(current.items)
        cancelled_items: list[OrderSnapshotItem] = []
        for identity, previous_items in sorted(previous_by_identity.items()):
            current_items = current_by_identity.get(identity, ())
            if len(previous_items) <= len(current_items):
                continue
            previous_counter = Counter(_item_content(item) for item in previous_items)
            current_counter = Counter(_item_content(item) for item in current_items)
            if any(
                current_counter[content] > previous_counter[content]
                for content in current_counter
            ):
                return _result(
                    status="CANCELLATION_AMBIGUOUS",
                    previous=previous,
                    current=current,
                    cancelled_order_count=None,
                    cancelled_qty=None,
                    reason="SAME_IDENTITY_CONTENT_CHANGED",
                )
            remaining = previous_counter - current_counter
            by_content: dict[
                tuple[int, str],
                list[OrderSnapshotItem],
            ] = defaultdict(list)
            for item in previous_items:
                by_content[_item_content(item)].append(item)
            for content, count in sorted(remaining.items()):
                cancelled_items.extend(
                    sorted(
                        by_content[content],
                        key=lambda item: (
                            item.occurrence_no,
                            item.observation_item_id,
                        ),
                    )[:count]
                )

        return _result(
            status="DETERMINED",
            previous=previous,
            current=current,
            cancelled_order_count=len(cancelled_items),
            cancelled_qty=sum(item.order_qty for item in cancelled_items),
            reason="MULTISET_DECREASE" if cancelled_items else "NO_DECREASE",
        )


def _comparison_ineligibility_reason(
    previous: OrderSnapshot,
    current: OrderSnapshot,
    *,
    are_adjacent_complete_snapshots: bool,
) -> str:
    if previous.platform_name != current.platform_name:
        return "PLATFORM_MISMATCH"
    if previous.platform_trade_date != current.platform_trade_date:
        return "TRADE_DAY_MISMATCH"
    if (
        previous.trade_day_status != "CLOSED"
        or current.trade_day_status != "CLOSED"
    ):
        return "OPEN_TRADE_DAY"
    if not are_adjacent_complete_snapshots:
        return "NOT_ADJACENT_COMPLETE_SNAPSHOTS"
    if previous.scan_completed_at >= current.scan_completed_at:
        return "INVALID_SNAPSHOT_ORDER"
    for snapshot in (previous, current):
        if (
            snapshot.capability_result != "SUCCEEDED"
            or snapshot.source_batch_status != "ACCEPTED"
            or not snapshot.scope_complete
            or not snapshot.end_marker_verified
        ):
            return "SNAPSHOT_INCOMPLETE"
    if any(
        item.mapping_status is not ProductMappingStatus.VERIFIED
        for snapshot in (previous, current)
        for item in snapshot.items
    ):
        return "MAPPING_NOT_VERIFIED"
    versions = {
        item.mapping_version
        for snapshot in (previous, current)
        for item in snapshot.items
    }
    if len(versions) > 1:
        return "MAPPING_VERSION_CHANGED"
    return ""


def _group_by_identity(
    items: tuple[OrderSnapshotItem, ...],
) -> dict[str, tuple[OrderSnapshotItem, ...]]:
    grouped: dict[str, list[OrderSnapshotItem]] = defaultdict(list)
    for item in items:
        grouped[item.order_identity_fingerprint].append(item)
    return {
        identity: tuple(values)
        for identity, values in grouped.items()
    }


def _item_content(item: OrderSnapshotItem) -> tuple[int, str]:
    return item.order_qty, _decimal_text(item.order_transaction_amount)


def _result(
    *,
    status: str,
    previous: OrderSnapshot,
    current: OrderSnapshot,
    cancelled_order_count: int | None,
    cancelled_qty: int | None,
    reason: str,
) -> OrderCancellationResult:
    payload = {
        "algorithm_version": ORDER_CANCELLATION_ALGORITHM_VERSION,
        "previous_batch_id": previous.observation_batch_id,
        "previous_content_sha256": previous.content_sha256,
        "current_batch_id": current.observation_batch_id,
        "current_content_sha256": current.content_sha256,
        "status": status,
        "cancelled_order_count": cancelled_order_count,
        "cancelled_qty": cancelled_qty,
        "reason": reason,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return OrderCancellationResult(
        status=status,
        previous_batch_id=previous.observation_batch_id,
        current_batch_id=current.observation_batch_id,
        cancelled_order_count=cancelled_order_count,
        cancelled_qty=cancelled_qty,
        comparison_sha256="sha256:" + hashlib.sha256(encoded).hexdigest(),
        reason=reason,
    )


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"

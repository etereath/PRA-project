from __future__ import annotations

from datetime import timezone
from decimal import Decimal
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from app.enums import DataQualityLevel, FactSource, ProductMappingStatus
from app.sales_settlement_models import (
    OrderSnapshot,
    OrderSnapshotItem,
    SalesEstimateSegment,
    SalesFactSelection,
)


SUPPORTED_SCOPE_TYPES = frozenset(
    {"PLATFORM", "VARIETY", "GRADE", "SKU", "TIME_BUCKET"}
)
MAPPING_DEPENDENT_SCOPE_TYPES = frozenset({"VARIETY", "SKU"})


class SalesFactSelectionService:
    """Choose one authoritative source without blending orders and estimates."""

    def select(
        self,
        *,
        platform_name: str,
        platform_trade_date,
        scope_type: str,
        scope_key: str,
        order_snapshots: Iterable[OrderSnapshot],
        estimate_segments: Iterable[SalesEstimateSegment],
        sku_dimensions: Mapping[str, Mapping[str, str]] | None = None,
    ) -> SalesFactSelection:
        scope = scope_type.strip().upper()
        if scope not in SUPPORTED_SCOPE_TYPES:
            raise ValueError(f"Unsupported settlement scope: {scope_type}")
        key = str(scope_key).strip()
        if not key:
            raise ValueError("scope_key must not be blank")
        dimensions = sku_dimensions or {}
        snapshots = tuple(
            snapshot
            for snapshot in order_snapshots
            if snapshot.platform_name == platform_name
            and snapshot.platform_trade_date == platform_trade_date
        )
        latest_complete = _latest_complete_snapshot(
            snapshots,
            scope_type=scope,
            sku_dimensions=dimensions,
        )
        if latest_complete is not None:
            return _selection_from_order(
                latest_complete,
                scope_type=scope,
                scope_key=key,
                quality_level=DataQualityLevel.ORDER_COMPLETE,
                sku_dimensions=dimensions,
            )

        latest_partial = _latest_partial_snapshot(snapshots)
        if latest_partial is not None:
            return _selection_from_order(
                latest_partial,
                scope_type=scope,
                scope_key=key,
                quality_level=DataQualityLevel.ORDER_PARTIAL,
                sku_dimensions=dimensions,
            )

        selected_segments = tuple(
            segment
            for segment in estimate_segments
            if segment.platform_name == platform_name
            and segment.platform_trade_date == platform_trade_date
            and segment.estimation_eligible
            and segment.estimated_sold_qty is not None
            and _segment_in_scope(
                segment,
                scope_type=scope,
                scope_key=key,
                sku_dimensions=dimensions,
            )
        )
        if selected_segments and not _segments_overlap(selected_segments):
            quality = (
                DataQualityLevel.SCAN_ESTIMATED_HIGH
                if all(
                    segment.quality_level
                    is DataQualityLevel.SCAN_ESTIMATED_HIGH
                    for segment in selected_segments
                )
                else DataQualityLevel.SCAN_ESTIMATED_MEDIUM
            )
            return SalesFactSelection(
                platform_name=platform_name,
                platform_trade_date=platform_trade_date,
                scope_type=scope,
                scope_key=key,
                fact_source=FactSource.SCAN_ESTIMATED,
                quality_level=quality,
                sold_qty=sum(
                    int(segment.estimated_sold_qty)
                    for segment in selected_segments
                ),
                order_count=None,
                transaction_amount_total=None,
                mapping_version=_common_mapping_version(
                    segment.mapping_version for segment in selected_segments
                ),
                quality_reason="ELIGIBLE_SCAN_ESTIMATE",
                source_proportions={"SCAN_ESTIMATED": 1.0},
                input_refs=tuple(
                    (
                        "SALES_ESTIMATE_SEGMENT",
                        segment.estimate_segment_id,
                        estimate_segment_sha256(segment),
                    )
                    for segment in sorted(
                        selected_segments,
                        key=lambda item: item.estimate_segment_id,
                    )
                ),
            )

        return SalesFactSelection(
            platform_name=platform_name,
            platform_trade_date=platform_trade_date,
            scope_type=scope,
            scope_key=key,
            fact_source=None,
            quality_level=DataQualityLevel.UNAVAILABLE,
            sold_qty=None,
            order_count=None,
            transaction_amount_total=None,
            mapping_version="",
            quality_reason=(
                "OVERLAPPING_ESTIMATE_SEGMENTS"
                if selected_segments
                else "NO_ACCEPTABLE_SALES_FACT"
            ),
            source_proportions={},
        )


def _latest_complete_snapshot(
    snapshots: tuple[OrderSnapshot, ...],
    *,
    scope_type: str,
    sku_dimensions: Mapping[str, Mapping[str, str]],
) -> OrderSnapshot | None:
    candidates = [
        snapshot
        for snapshot in snapshots
        if snapshot.trade_day_status == "CLOSED"
        and snapshot.capability_result == "SUCCEEDED"
        and snapshot.source_batch_status == "ACCEPTED"
        and snapshot.scope_complete
        and snapshot.end_marker_verified
        and _mapping_complete_for_scope(
            snapshot,
            scope_type=scope_type,
            sku_dimensions=sku_dimensions,
        )
    ]
    return max(
        candidates,
        key=lambda item: (
            item.scan_completed_at,
            item.observation_batch_id,
        ),
        default=None,
    )


def _latest_partial_snapshot(
    snapshots: tuple[OrderSnapshot, ...],
) -> OrderSnapshot | None:
    candidates = [
        snapshot
        for snapshot in snapshots
        if snapshot.capability_result == "SUCCEEDED"
        and snapshot.batch_status in {"ACCEPTED", "PARTIAL"}
    ]
    return max(
        candidates,
        key=lambda item: (
            item.scan_completed_at,
            item.observation_batch_id,
        ),
        default=None,
    )


def _mapping_complete_for_scope(
    snapshot: OrderSnapshot,
    *,
    scope_type: str,
    sku_dimensions: Mapping[str, Mapping[str, str]],
) -> bool:
    if scope_type not in MAPPING_DEPENDENT_SCOPE_TYPES:
        return True
    if any(
        item.mapping_status is not ProductMappingStatus.VERIFIED
        or not item.internal_sku
        for item in snapshot.items
    ):
        return False
    versions = {item.mapping_version for item in snapshot.items}
    if len(versions) > 1:
        return False
    if scope_type == "VARIETY":
        return all(
            item.internal_sku in sku_dimensions
            and bool(sku_dimensions[item.internal_sku].get("variety", "").strip())
            for item in snapshot.items
            if item.internal_sku is not None
        )
    return True


def _selection_from_order(
    snapshot: OrderSnapshot,
    *,
    scope_type: str,
    scope_key: str,
    quality_level: DataQualityLevel,
    sku_dimensions: Mapping[str, Mapping[str, str]],
) -> SalesFactSelection:
    scoped_items = tuple(
        item
        for item in snapshot.items
        if _item_in_scope(
            item,
            scope_type=scope_type,
            scope_key=scope_key,
            sku_dimensions=sku_dimensions,
        )
    )
    mapping_version = (
        snapshot.mapping_version
        if scope_type in MAPPING_DEPENDENT_SCOPE_TYPES
        else _common_mapping_version(item.mapping_version for item in scoped_items)
    )
    return SalesFactSelection(
        platform_name=snapshot.platform_name,
        platform_trade_date=snapshot.platform_trade_date,
        scope_type=scope_type,
        scope_key=scope_key,
        fact_source=FactSource.ORDER_OBSERVED,
        quality_level=quality_level,
        sold_qty=sum(item.order_qty for item in scoped_items),
        order_count=len(scoped_items),
        transaction_amount_total=sum(
            (item.order_transaction_amount for item in scoped_items),
            Decimal("0"),
        ),
        mapping_version=mapping_version,
        quality_reason=(
            "COMPLETE_CLOSED_ORDER_SNAPSHOT"
            if quality_level is DataQualityLevel.ORDER_COMPLETE
            else "PARTIAL_OR_OPEN_ORDER_SNAPSHOT"
        ),
        source_proportions={"ORDER_OBSERVED": 1.0},
        input_refs=(
            (
                "ORDER_OBSERVATION_BATCH",
                snapshot.observation_batch_id,
                snapshot.content_sha256,
            ),
        ),
        selected_order_batch_id=snapshot.observation_batch_id,
        selected_order_batch_status=snapshot.trade_day_status,
    )


def _item_in_scope(
    item: OrderSnapshotItem,
    *,
    scope_type: str,
    scope_key: str,
    sku_dimensions: Mapping[str, Mapping[str, str]],
) -> bool:
    if scope_type == "PLATFORM":
        return True
    if scope_type == "GRADE":
        return item.grade.strip() == scope_key
    if scope_type == "SKU":
        return item.internal_sku == scope_key
    if scope_type == "VARIETY":
        return bool(
            item.internal_sku
            and sku_dimensions.get(item.internal_sku, {}).get("variety", "").strip()
            == scope_key
        )
    if scope_type == "TIME_BUCKET":
        start_hour, end_hour = _parse_time_bucket(scope_key)
        local_hour = item.order_created_at.astimezone(
            ZoneInfo("Asia/Shanghai")
        ).hour
        return start_hour <= local_hour <= end_hour
    return False


def _segment_in_scope(
    segment: SalesEstimateSegment,
    *,
    scope_type: str,
    scope_key: str,
    sku_dimensions: Mapping[str, Mapping[str, str]],
) -> bool:
    if scope_type == "PLATFORM":
        return True
    if scope_type == "SKU":
        return segment.internal_sku == scope_key
    if scope_type == "VARIETY":
        return (
            sku_dimensions.get(segment.internal_sku, {}).get("variety", "").strip()
            == scope_key
        )
    return False


def _parse_time_bucket(value: str) -> tuple[int, int]:
    try:
        start, end = value.split("-", maxsplit=1)
        start_hour = int(start.split(":", maxsplit=1)[0])
        end_hour = int(end.split(":", maxsplit=1)[0])
    except (ValueError, IndexError) as exc:
        raise ValueError(
            "TIME_BUCKET scope_key must look like HH:MM-HH:MM"
        ) from exc
    if not (0 <= start_hour <= end_hour <= 23):
        raise ValueError("TIME_BUCKET hours are invalid")
    return start_hour, end_hour


def _segments_overlap(segments: tuple[SalesEstimateSegment, ...]) -> bool:
    by_sku: dict[str, list[SalesEstimateSegment]] = {}
    for segment in segments:
        by_sku.setdefault(segment.internal_sku, []).append(segment)
    for values in by_sku.values():
        ordered = sorted(values, key=lambda item: item.interval_started_at)
        if any(
            current.interval_started_at < previous.interval_ended_at
            for previous, current in zip(ordered, ordered[1:])
        ):
            return True
    return False


def _common_mapping_version(values: Iterable[str]) -> str:
    versions = {str(value) for value in values if str(value)}
    return next(iter(versions)) if len(versions) == 1 else ""


def estimate_segment_sha256(segment: SalesEstimateSegment) -> str:
    import hashlib
    import json

    payload = {
        "estimate_segment_id": segment.estimate_segment_id,
        "platform_name": segment.platform_name,
        "internal_sku": segment.internal_sku,
        "platform_trade_date": segment.platform_trade_date.isoformat(),
        "interval_started_at": segment.interval_started_at.astimezone(
            timezone.utc
        ).isoformat(),
        "interval_ended_at": segment.interval_ended_at.astimezone(
            timezone.utc
        ).isoformat(),
        "inventory_before": segment.inventory_before,
        "inventory_after": segment.inventory_after,
        "known_inventory_adjustment": segment.known_inventory_adjustment,
        "estimated_sold_qty": segment.estimated_sold_qty,
        "estimation_eligible": segment.estimation_eligible,
        "estimation_reason": segment.estimation_reason,
        "quality_level": segment.quality_level.value,
        "mapping_version": segment.mapping_version,
        "supporting_observation_ids": list(
            segment.supporting_observation_ids
        ),
        "algorithm_version": segment.algorithm_version,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()

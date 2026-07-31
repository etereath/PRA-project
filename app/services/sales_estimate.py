from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping

from app.enums import DataQualityLevel, ProductMappingStatus
from app.sales_settlement_models import (
    InventoryAdjustmentSourceRef,
    InventoryObservationPoint,
    SalesEstimateSegment,
)
from app.services.operational_time import OperationalTimeService


SALES_ESTIMATE_ALGORITHM_VERSION = "sales-estimate-v1-15m-25m"
HIGH_MAX_GAP = timedelta(minutes=15)
MEDIUM_MAX_GAP = timedelta(minutes=25)
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

ADJUSTMENT_SOURCE_TYPES = frozenset(
    {
        "PRA_INVENTORY_WRITE",
        "MANUAL_PLATFORM_MODIFICATION",
        "SET_ONLINE_INVENTORY_RESET",
        "TARGET_INVENTORY",
        "RECONCILIATION_CORRECTION",
        "ADJUSTMENT_COVERAGE_ATTESTATION",
        "UNKNOWN_INVENTORY_INCREASE",
        "UNKNOWN_INVENTORY_DECREASE",
    }
)
COUNTED_ADJUSTMENT_SOURCE_TYPES = frozenset(
    {
        "PRA_INVENTORY_WRITE",
        "MANUAL_PLATFORM_MODIFICATION",
        "SET_ONLINE_INVENTORY_RESET",
        "RECONCILIATION_CORRECTION",
    }
)


class SalesEstimateError(ValueError):
    """Raised when immutable estimate evidence is malformed."""


class SalesEstimateService:
    """Build append-only inventory-decrease estimates conservatively."""

    def __init__(
        self,
        *,
        operational_time: OperationalTimeService | None = None,
        clock: Callable[[], datetime] | None = None,
        algorithm_version: str = SALES_ESTIMATE_ALGORITHM_VERSION,
    ) -> None:
        self.operational_time = operational_time or OperationalTimeService()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.algorithm_version = _require_text(
            algorithm_version,
            "algorithm_version",
        )

    def calculate_segment(
        self,
        before: InventoryObservationPoint,
        after: InventoryObservationPoint,
        *,
        adjustments: Iterable[InventoryAdjustmentSourceRef] = (),
        unresolved_inventory_write: bool = False,
        conflicting_observation: bool = False,
        critical_scan_failure: bool = False,
        overlaps_existing: bool = False,
        merged_full_scan_replacement: bool = False,
    ) -> SalesEstimateSegment:
        inventory_before = _required_inventory(
            before.observed_inventory,
            "inventory_before",
        )
        inventory_after = _required_inventory(
            after.observed_inventory,
            "inventory_after",
        )
        refs = _normalize_adjustments(adjustments, before, after)
        known_adjustment = _known_adjustment(refs)
        reason = self._ineligibility_reason(
            before,
            after,
            refs=refs,
            known_adjustment=known_adjustment,
            unresolved_inventory_write=unresolved_inventory_write,
            conflicting_observation=conflicting_observation,
            critical_scan_failure=critical_scan_failure,
            overlaps_existing=overlaps_existing,
        )
        gap = after.observed_at - before.observed_at
        if reason is None and gap > MEDIUM_MAX_GAP:
            reason = "SCAN_GAP_EXCEEDED"

        quality_level = DataQualityLevel.SCAN_ESTIMATED_LOW
        estimated_sold_qty = None
        eligible = reason is None
        if eligible:
            residual = (
                inventory_before
                - inventory_after
                - known_adjustment
            )
            if residual < 0:
                reason = "ADJUSTMENT_DOES_NOT_RECONCILE"
                eligible = False
            else:
                estimated_sold_qty = max(residual, 0)
                if gap <= HIGH_MAX_GAP and not merged_full_scan_replacement:
                    quality_level = DataQualityLevel.SCAN_ESTIMATED_HIGH
                else:
                    quality_level = DataQualityLevel.SCAN_ESTIMATED_MEDIUM
                reason = (
                    "ELIGIBLE_KNOWN_ADJUSTMENT"
                    if known_adjustment != 0
                    else "ELIGIBLE_NO_ADJUSTMENT"
                )

        if not eligible:
            quality_level = DataQualityLevel.SCAN_ESTIMATED_LOW
            estimated_sold_qty = None

        internal_sku = before.internal_sku or after.internal_sku or ""
        return SalesEstimateSegment(
            estimate_segment_id=_segment_id(
                before,
                after,
                algorithm_version=self.algorithm_version,
            ),
            platform_name=before.platform_name,
            internal_sku=internal_sku,
            platform_trade_date=before.platform_trade_date,
            interval_started_at=before.observed_at,
            interval_ended_at=after.observed_at,
            inventory_before=inventory_before,
            inventory_after=inventory_after,
            known_inventory_adjustment=known_adjustment,
            known_adjustment_source_refs=refs,
            estimated_sold_qty=estimated_sold_qty,
            estimation_eligible=eligible,
            estimation_reason=reason or "INVALID_INTERVAL",
            quality_level=quality_level,
            mapping_version=(
                before.mapping_version
                if before.mapping_version == after.mapping_version
                else ""
            ),
            supporting_observation_ids=(
                before.observation_item_id,
                after.observation_item_id,
            ),
            algorithm_version=self.algorithm_version,
            created_at=_as_utc(self.clock(), "clock"),
        )

    def build_adjacent_segments(
        self,
        observations: Iterable[InventoryObservationPoint],
        *,
        adjustments_by_interval: Mapping[
            tuple[str, str],
            Iterable[InventoryAdjustmentSourceRef],
        ]
        | None = None,
        unresolved_intervals: Iterable[tuple[str, str]] = (),
        failed_intervals: Iterable[tuple[str, str]] = (),
    ) -> tuple[SalesEstimateSegment, ...]:
        grouped: dict[
            tuple[str, str, object],
            list[InventoryObservationPoint],
        ] = defaultdict(list)
        for observation in observations:
            grouped[
                (
                    observation.platform_name,
                    observation.internal_sku or "",
                    observation.platform_trade_date,
                )
            ].append(observation)

        adjustment_map = adjustments_by_interval or {}
        unresolved = set(unresolved_intervals)
        failed = set(failed_intervals)
        segments: list[SalesEstimateSegment] = []
        for points in grouped.values():
            ordered = sorted(
                points,
                key=lambda item: (
                    item.observed_at,
                    item.observation_item_id,
                ),
            )
            conflicts_by_instant: set[datetime] = set()
            by_instant: dict[datetime, list[InventoryObservationPoint]] = (
                defaultdict(list)
            )
            for point in ordered:
                by_instant[point.observed_at].append(point)
            for observed_at, simultaneous in by_instant.items():
                if len(
                    {
                        (
                            point.observed_inventory,
                            point.observed_online,
                            point.mapping_status,
                            point.mapping_version,
                        )
                        for point in simultaneous
                    }
                ) > 1:
                    conflicts_by_instant.add(observed_at)
            for before, after in zip(ordered, ordered[1:]):
                if after.observed_at <= before.observed_at:
                    continue
                if (
                    before.observed_inventory is None
                    or after.observed_inventory is None
                ):
                    continue
                identity = (
                    before.observation_item_id,
                    after.observation_item_id,
                )
                endpoint_conflict = (
                    before.observed_at in conflicts_by_instant
                    or after.observed_at in conflicts_by_instant
                )
                segments.append(
                    self.calculate_segment(
                        before,
                        after,
                        adjustments=adjustment_map.get(identity, ()),
                        unresolved_inventory_write=identity in unresolved,
                        critical_scan_failure=identity in failed,
                        conflicting_observation=endpoint_conflict,
                    )
                )
        return tuple(
            sorted(
                segments,
                key=lambda item: (
                    item.platform_name,
                    item.internal_sku,
                    item.platform_trade_date,
                    item.interval_started_at,
                    item.interval_ended_at,
                    item.estimate_segment_id,
                ),
            )
        )

    def _ineligibility_reason(
        self,
        before: InventoryObservationPoint,
        after: InventoryObservationPoint,
        *,
        refs: tuple[InventoryAdjustmentSourceRef, ...],
        known_adjustment: int,
        unresolved_inventory_write: bool,
        conflicting_observation: bool,
        critical_scan_failure: bool,
        overlaps_existing: bool,
    ) -> str | None:
        if after.observed_at <= before.observed_at:
            return "INVALID_INTERVAL"
        if (
            before.platform_name != after.platform_name
            or before.internal_sku != after.internal_sku
            or not before.internal_sku
        ):
            return "PLATFORM_OR_SKU_MISMATCH"
        if before.platform_trade_date != after.platform_trade_date:
            return "TRADE_DAY_MISMATCH"
        before_time = self.operational_time.classify(before.observed_at)
        after_time = self.operational_time.classify(after.observed_at)
        if (
            before_time.platform_trade_date
            != after_time.platform_trade_date
            or before_time.platform_trade_date
            != before.platform_trade_date
        ):
            return "CROSSED_PLATFORM_CUTOFF"
        if critical_scan_failure or any(
            point.batch_status != "ACCEPTED"
            or not point.scope_complete
            or not point.end_marker_verified
            for point in (before, after)
        ):
            return "OBSERVATION_INCOMPLETE"
        if (
            before.observed_inventory is None
            or after.observed_inventory is None
            or before.observed_inventory < 0
            or after.observed_inventory < 0
        ):
            return "INVENTORY_UNREADABLE"
        if any(
            point.mapping_status is not ProductMappingStatus.VERIFIED
            for point in (before, after)
        ):
            return "MAPPING_NOT_VERIFIED"
        if before.mapping_version != after.mapping_version:
            return "MAPPING_VERSION_CHANGED"
        if (
            not before.observed_online
            or not after.observed_online
            or any(
                ref.source_type == "SET_ONLINE_INVENTORY_RESET"
                for ref in refs
            )
        ):
            return "NOT_CONTINUOUSLY_ONLINE"
        if conflicting_observation:
            return "CONFLICTING_OBSERVATION"
        if unresolved_inventory_write:
            return "UNRESOLVED_INVENTORY_WRITE"
        if _has_unverified_target_inventory(refs):
            return "TARGET_INVENTORY_NOT_VERIFIED"
        if any(
            ref.source_type == "MANUAL_PLATFORM_MODIFICATION"
            and ref.adjustment_qty == 0
            for ref in refs
        ):
            return "MANUAL_CHANGE_UNQUANTIFIED"
        if not any(
            ref.source_type == "ADJUSTMENT_COVERAGE_ATTESTATION"
            for ref in refs
        ):
            return "ADJUSTMENT_COVERAGE_UNPROVEN"
        if any(
            ref.source_type == "UNKNOWN_INVENTORY_INCREASE" for ref in refs
        ):
            return "UNKNOWN_INVENTORY_INCREASE"
        if any(
            ref.source_type == "UNKNOWN_INVENTORY_DECREASE" for ref in refs
        ):
            return "UNKNOWN_INVENTORY_DECREASE"
        if (
            before.observed_inventory is not None
            and after.observed_inventory is not None
            and before.observed_inventory
            - after.observed_inventory
            - known_adjustment
            < 0
        ):
            return "ADJUSTMENT_DOES_NOT_RECONCILE"
        if overlaps_existing:
            return "OVERLAPPING_INTERVAL"
        return None


def _normalize_adjustments(
    adjustments: Iterable[InventoryAdjustmentSourceRef],
    before: InventoryObservationPoint,
    after: InventoryObservationPoint,
) -> tuple[InventoryAdjustmentSourceRef, ...]:
    normalized: list[InventoryAdjustmentSourceRef] = []
    seen_refs: set[str] = set()
    for ref in adjustments:
        if ref.source_type not in ADJUSTMENT_SOURCE_TYPES:
            raise SalesEstimateError(
                f"Unsupported inventory adjustment source: {ref.source_type}"
            )
        _require_text(ref.adjustment_id, "adjustment_id")
        source_ref_id = _require_text(ref.source_ref_id, "source_ref_id")
        if source_ref_id in seen_refs:
            raise SalesEstimateError(
                "Inventory adjustment source_ref_id must be unique"
            )
        seen_refs.add(source_ref_id)
        occurred_at = _as_utc(ref.occurred_at, "occurred_at")
        if not (before.observed_at < occurred_at <= after.observed_at):
            raise SalesEstimateError(
                "Inventory adjustment must fall within the estimate interval"
            )
        if isinstance(ref.adjustment_qty, bool) or not isinstance(
            ref.adjustment_qty,
            int,
        ):
            raise SalesEstimateError("adjustment_qty must be an integer")
        if ref.source_type in {
            "ADJUSTMENT_COVERAGE_ATTESTATION",
            "UNKNOWN_INVENTORY_INCREASE",
            "UNKNOWN_INVENTORY_DECREASE",
            "TARGET_INVENTORY",
        } and ref.adjustment_qty != 0:
            raise SalesEstimateError(
                f"{ref.source_type} adjustment_qty must be zero"
            )
        if not SHA256_RE.fullmatch(ref.evidence_sha256):
            raise SalesEstimateError(
                "Inventory adjustment evidence_sha256 is invalid"
            )
        normalized.append(ref)
    return tuple(
        sorted(
            normalized,
            key=lambda item: (
                item.source_ref_id,
                item.source_type,
                item.adjustment_id,
            ),
        )
    )


def _known_adjustment(
    refs: tuple[InventoryAdjustmentSourceRef, ...],
) -> int:
    grouped: dict[str, list[InventoryAdjustmentSourceRef]] = defaultdict(list)
    for ref in refs:
        grouped[ref.adjustment_id].append(ref)
    total = 0
    for group in grouped.values():
        counted = [
            ref
            for ref in group
            if ref.source_type in COUNTED_ADJUSTMENT_SOURCE_TYPES
        ]
        quantities = {ref.adjustment_qty for ref in counted}
        if len(quantities) > 1:
            raise SalesEstimateError(
                "One physical inventory adjustment has conflicting quantities"
            )
        if quantities:
            total += next(iter(quantities))
    return total


def _has_unverified_target_inventory(
    refs: tuple[InventoryAdjustmentSourceRef, ...],
) -> bool:
    grouped: dict[str, set[str]] = defaultdict(set)
    for ref in refs:
        grouped[ref.adjustment_id].add(ref.source_type)
    return any(
        "TARGET_INVENTORY" in types
        and not types.intersection(COUNTED_ADJUSTMENT_SOURCE_TYPES)
        for types in grouped.values()
    )


def _segment_id(
    before: InventoryObservationPoint,
    after: InventoryObservationPoint,
    *,
    algorithm_version: str,
) -> str:
    identity = "\x1f".join(
        (
            before.platform_name,
            before.internal_sku or "",
            before.platform_trade_date.isoformat(),
            before.observation_item_id,
            after.observation_item_id,
            algorithm_version,
        )
    )
    return "estimate-segment-v1-" + hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SalesEstimateError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _require_text(value: str, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise SalesEstimateError(f"{field_name} must not be blank")
    return normalized


def _required_inventory(value: int | None, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SalesEstimateError(
            f"{field_name} must be a readable non-negative integer"
        )
    return value

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.enums import DataQualityLevel, FactSource, ProductMappingStatus


@dataclass(frozen=True, slots=True)
class InventoryObservationPoint:
    observation_item_id: str
    observation_batch_id: str
    platform_name: str
    internal_sku: str | None
    platform_trade_date: date
    observed_at: datetime
    observed_price: Decimal | None
    observed_inventory: int | None
    observed_online: bool
    mapping_status: ProductMappingStatus
    mapping_version: str
    scan_type: str
    batch_status: str
    scope_complete: bool
    end_marker_verified: bool
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ProductScanExecution:
    automation_run_id: str
    observation_batch_id: str | None
    run_status: str
    batch_status: str | None
    scan_started_at: datetime
    scan_completed_at: datetime
    scope_complete: bool
    end_marker_verified: bool

    @property
    def critical_failure(self) -> bool:
        return (
            self.run_status in {"FAILED", "MISSED"}
            or self.batch_status in {"PARTIAL", "UNAVAILABLE", "FAILED"}
            or not self.scope_complete
            or not self.end_marker_verified
        )


@dataclass(frozen=True, slots=True)
class InventoryAdjustmentSourceRef:
    adjustment_id: str
    source_type: str
    source_ref_id: str
    adjustment_qty: int
    occurred_at: datetime
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class SalesEstimateSegment:
    estimate_segment_id: str
    platform_name: str
    internal_sku: str
    platform_trade_date: date
    interval_started_at: datetime
    interval_ended_at: datetime
    inventory_before: int
    inventory_after: int
    known_inventory_adjustment: int
    known_adjustment_source_refs: tuple[
        InventoryAdjustmentSourceRef, ...
    ]
    estimated_sold_qty: int | None
    estimation_eligible: bool
    estimation_reason: str
    quality_level: DataQualityLevel
    mapping_version: str
    supporting_observation_ids: tuple[str, ...]
    algorithm_version: str
    created_at: datetime

    @property
    def confidence(self) -> str:
        return {
            DataQualityLevel.SCAN_ESTIMATED_HIGH: "HIGH",
            DataQualityLevel.SCAN_ESTIMATED_MEDIUM: "MEDIUM",
            DataQualityLevel.SCAN_ESTIMATED_LOW: "LOW",
        }[self.quality_level]


@dataclass(frozen=True, slots=True)
class OrderSnapshotItem:
    observation_item_id: str
    order_identity_fingerprint: str
    occurrence_no: int
    order_created_at: datetime
    platform_product_name: str
    grade: str
    internal_sku: str | None
    mapping_status: ProductMappingStatus
    mapping_version: str
    order_qty: int
    order_transaction_amount: Decimal
    raw_observation_sha256: str


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    observation_batch_id: str
    platform_name: str
    platform_trade_date: date
    trade_day_status: str
    capability_result: str
    batch_status: str
    source_batch_status: str
    scope_complete: bool
    end_marker_verified: bool
    scan_started_at: datetime
    scan_completed_at: datetime
    content_sha256: str
    time_policy_version: str
    mapping_version: str
    items: tuple[OrderSnapshotItem, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class OrderCancellationResult:
    status: str
    previous_batch_id: str
    current_batch_id: str
    cancelled_order_count: int | None
    cancelled_qty: int | None
    comparison_sha256: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SalesFactSelection:
    platform_name: str
    platform_trade_date: date
    scope_type: str
    scope_key: str
    fact_source: FactSource | None
    quality_level: DataQualityLevel
    sold_qty: int | None
    order_count: int | None
    transaction_amount_total: Decimal | None
    mapping_version: str
    algorithm_version: str
    quality_reason: str
    source_proportions: dict[str, Any]
    input_refs: tuple[tuple[str, str, str], ...] = field(
        default_factory=tuple
    )
    selected_order_batch_id: str | None = None
    selected_order_batch_status: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationDecision:
    decision_ref_id: str
    decision_sha256: str
    classification: str
    difference_qty: int | None


@dataclass(frozen=True, slots=True)
class SalesPlanInputManifest:
    platform_name: str
    settled_platform_trade_date: date
    plan_for_seller_operation_date: date
    projection_role: str
    payload: dict[str, Any]
    input_refs: tuple[tuple[str, str, str], ...]
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class SettlementSnapshot:
    platform_name: str
    platform_trade_date: date
    seller_operation_date: date
    summaries: tuple[dict[str, Any], ...]
    sales_plan_input: SalesPlanInputManifest
    management_report: dict[str, Any]
    audit_receipt: dict[str, Any]
    snapshot_sha256: str

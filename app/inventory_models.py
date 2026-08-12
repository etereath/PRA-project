from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class InventoryAuthorityState:
    authority_mode: str
    bootstrap_snapshot_sha256: str | None
    bootstrap_runtime_snapshot_sha256: str | None
    bootstrap_sales_watermark_date: date | None
    bootstrap_idempotency_key: str | None
    bootstrap_completed_at: datetime | None
    bootstrap_completed_by: str | None
    version: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class InventoryBalance:
    internal_sku: str
    current_qty: int
    version: int
    last_transaction_id: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class InventoryTransaction:
    transaction_id: str
    internal_sku: str
    inventory_before: int
    inventory_delta: int
    inventory_after: int
    transaction_type: str
    source_type: str
    source_ref_id: str
    reason: str
    actor: str
    seller_operation_date: date | None
    platform_name: str | None
    platform_trade_date: date | None
    supporting_refs: tuple[str, ...]
    idempotency_key: str
    request_sha256: str
    balance_version_after: int
    occurred_at: datetime
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class InventorySalesBaseline:
    platform_name: str
    platform_trade_date: date
    internal_sku: str
    selected_fact_source: str
    quality_level: str
    selected_sold_qty: int
    source_ref_id: str
    source_sha256: str
    mapping_version: str
    supporting_refs: tuple[str, ...]
    inventory_transaction_id: str
    version: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class InventoryAlertPolicy:
    policy_key: str
    scope_type: str
    scope_key: str
    enabled: bool
    threshold_qty: int
    repeat_interval_minutes: int
    version: int
    updated_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class InventoryWriteResult:
    status: str
    transaction: InventoryTransaction | None
    balance: InventoryBalance | None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class InventoryBootstrapResult:
    status: str
    authority_state: InventoryAuthorityState
    balance_count: int
    sales_baseline_count: int
    transaction_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InventorySalesBatchResult:
    status: str
    applied_sku_count: int
    replayed_sku_count: int
    skipped_sku_count: int
    transaction_ids: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InventoryAlertResult:
    status: str
    internal_sku: str
    current_qty: int
    threshold_qty: int | None
    incident_id: str | None = None
    notification_id: str | None = None

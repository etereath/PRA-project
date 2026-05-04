from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.enums import (
    ConditionType,
    ListingAction,
    PricingMethod,
    PricingSource,
    ReviewTaskStatus,
    RoundingRule,
    ShortageRisk,
    TaskActionType,
    TaskStatus,
    TradePhase,
)


@dataclass(slots=True)
class Product:
    internal_sku: str
    product_name: str
    grade: str
    stem_length: str
    unit: str
    base_cost: Decimal
    current_stock: int
    sale_enabled: bool
    remark: str = ""
    last_price: Decimal | None = None
    recommended_price: Decimal | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PriceRule:
    rule_id: str
    rule_name: str
    scope_type: str
    scope_value: str
    pricing_method: PricingMethod
    markup_value: Decimal
    min_price: Decimal | None
    rounding_rule: RoundingRule
    rounding_step: Decimal | None
    active: bool
    priority: int
    remark: str = ""


@dataclass(slots=True)
class ListingRule:
    rule_id: str
    rule_name: str
    condition_type: ConditionType
    condition_value: Decimal | str | None
    action: ListingAction
    active: bool
    priority: int
    remark: str = ""


@dataclass(slots=True)
class PlatformMapping:
    internal_sku: str
    platform_name: str
    platform_product_id: str
    platform_product_name: str
    mapping_status: str = "reserved"


@dataclass(slots=True)
class HarvestForecast:
    forecast_id: str
    forecast_date: date
    target_trade_date: date
    forecast_group_key: str
    variety: str
    grade: str
    predicted_harvest_qty: int
    lower_bound_qty: int | None = None
    upper_bound_qty: int | None = None
    confidence: Decimal | None = None
    source: str = "manual"
    generated_at: datetime | None = None
    note: str = ""


@dataclass(slots=True)
class PriceForecast:
    forecast_id: str
    forecast_date: date
    target_trade_date: date
    forecast_group_key: str
    variety: str
    grade: str
    recommended_price: Decimal
    lower_bound_price: Decimal | None = None
    upper_bound_price: Decimal | None = None
    confidence: Decimal | None = None
    source: str = "manual"
    generated_at: datetime | None = None
    note: str = ""


@dataclass(slots=True)
class TradeWindow:
    trade_date: date
    trade_open_at: datetime
    clearance_start_at: datetime
    trade_close_at: datetime
    phase: TradePhase


@dataclass(slots=True)
class PackingCapacityPlan:
    trade_date: date
    normal_packing_capacity_qty: int = 250
    temp_worker_capacity_qty: int = 100
    confirmed_temp_worker_count: int = 0
    allocation_rule: str = "proportional_by_forecast"
    listing_quota: dict[str, int] = field(default_factory=dict)
    note: str = ""

    @property
    def confirmed_temp_labor_capacity_qty(self) -> int:
        return self.confirmed_temp_worker_count * self.temp_worker_capacity_qty

    @property
    def confirmed_packing_capacity_qty(self) -> int:
        return self.normal_packing_capacity_qty + self.confirmed_temp_labor_capacity_qty


@dataclass(slots=True)
class ColdStorageStatus:
    trade_date: date
    cold_storage_total_capacity_qty: int = 500
    cold_storage_current_qty: int = 0
    note: str = ""

    @property
    def cold_storage_available_capacity(self) -> int:
        return self.cold_storage_total_capacity_qty - self.cold_storage_current_qty


@dataclass(slots=True)
class InventoryPlan:
    forecast_group_key: str
    trade_date: date
    actual_stock_qty: int
    predicted_harvest_qty: int
    reserved_qty: int
    safety_buffer_qty: int
    field_buffer_qty: int
    allocated_packing_capacity_qty: int
    inventory_based_available_qty: int
    risk_adjusted_available_qty: int
    committable_qty: int
    shortage_risk: ShortageRisk
    decision_trace: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ListingDecision:
    internal_sku: str
    trade_date: date
    forecast_group_key: str
    committable_qty: int
    should_online: bool
    should_offline: bool
    shortage_risk: ShortageRisk
    reason: str
    decision_trace: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PricingDecision:
    internal_sku: str
    trade_date: date
    forecast_group_key: str
    recommended_price: Decimal | None
    target_price: Decimal | None
    pricing_source: PricingSource
    requires_manual_review: bool
    review_reason: str
    decision_trace: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlatformPriceRule:
    platform_name: str
    forecast_group_key: str
    price_factor: Decimal = Decimal("1")
    fixed_adjustment: Decimal = Decimal("0")
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    rounding_rule: RoundingRule = RoundingRule.NONE
    active: bool = True
    remark: str = ""


@dataclass(slots=True)
class ReviewRequirement:
    task_type: TaskActionType
    internal_sku: str
    trade_date: date
    reason: str
    required_by: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RulePricingInput:
    product: Product
    platform_name: str
    rules: list[PriceRule]


@dataclass(slots=True)
class RulePricingResult:
    matched_rule_ids: list[str]
    matched_rule_names: list[str]
    rule_price: Decimal
    applied_steps: list[str]


@dataclass(slots=True)
class AISuggestionInput:
    internal_sku: str
    product_name: str
    platform_name: str
    cost: Decimal
    stock: int
    last_price: Decimal | None
    features: dict[str, Any]
    time_window: str = "current_snapshot"


@dataclass(slots=True)
class AISuggestionResult:
    suggested_price: Decimal | None
    confidence: Decimal | None
    reason: str
    model_version: str


@dataclass(slots=True)
class FinalPricingDecision:
    internal_sku: str
    platform_name: str
    rule_price: Decimal
    final_price: Decimal
    pricing_source: PricingSource
    decision_trace: dict[str, Any]
    ai_suggestion: AISuggestionResult | None


@dataclass(slots=True)
class Task:
    task_id: str
    internal_sku: str | None
    platform_name: str | None
    action_type: TaskActionType
    priority: int
    task_status: TaskStatus
    created_at: datetime
    target_price: Decimal | None = None
    target_status: str | None = None
    pricing_source: PricingSource | None = None
    decision_trace: dict[str, Any] = field(default_factory=dict)
    result_message: str = ""
    required_by: datetime | None = None
    trade_date: date | None = None
    scope_type: str = "sku"
    scope_key: str = ""
    dedupe_key: str = ""
    scheduled_at: datetime | None = None
    expires_at: datetime | None = None
    updated_at: datetime | None = None

    def to_record(self) -> dict[str, Any]:
        data = asdict(self)
        data["action_type"] = self.action_type.value
        data["task_status"] = self.task_status.value
        data["pricing_source"] = self.pricing_source.value if self.pricing_source else None
        data["created_at"] = self.created_at.isoformat()
        data["required_by"] = self.required_by.isoformat() if self.required_by else None
        data["trade_date"] = self.trade_date.isoformat() if self.trade_date else None
        data["scheduled_at"] = self.scheduled_at.isoformat() if self.scheduled_at else None
        data["expires_at"] = self.expires_at.isoformat() if self.expires_at else None
        data["updated_at"] = self.updated_at.isoformat() if self.updated_at else None
        return data


@dataclass(slots=True)
class ExecutionLog:
    log_id: str
    task_id: str
    executor_name: str
    start_time: datetime
    end_time: datetime | None
    success_flag: bool | None
    error_code: str = ""
    error_message: str = ""
    raw_output: str = ""
    ai_model_version: str = ""
    ai_summary: str = ""
    created_at: datetime | None = None


@dataclass(slots=True)
class ReviewTask:
    review_task_id: str
    trade_date: date | None
    scope_type: str
    scope_key: str
    dedupe_key: str
    source_task_id: str | None
    review_type: str
    review_status: ReviewTaskStatus
    internal_sku: str | None = None
    platform_name: str | None = None
    reason: str = ""
    review_payload: dict[str, Any] = field(default_factory=dict)
    resolution_payload: dict[str, Any] = field(default_factory=dict)
    required_by: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    resolved_by: str = ""
    resolved_at: datetime | None = None
    resolution_note: str = ""


@dataclass(slots=True)
class NotificationLog:
    notification_id: str
    related_task_id: str | None
    related_review_task_id: str | None
    recipient_type: str
    recipient: str
    channel: str
    sent_at: datetime | None
    send_status: str
    dedupe_key: str
    message: str
    error_message: str = ""
    created_at: datetime | None = None


@dataclass(slots=True)
class TaskStatusHistory:
    history_id: str
    task_id: str
    from_status: TaskStatus | None
    to_status: TaskStatus
    changed_by: str
    changed_at: datetime
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

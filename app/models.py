from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.enums import (
    ConditionType,
    ListingAction,
    PricingMethod,
    PricingSource,
    RoundingRule,
    TaskActionType,
    TaskStatus,
)


@dataclass(slots=True)
class Product:
    internal_sku: str
    product_name: str
    variety: str
    grade: str
    stem_length: str
    unit: str
    base_cost: Decimal
    current_stock: int
    sale_enabled: bool
    remark: str = ""
    last_price: Decimal | None = None
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
    condition_value: Decimal | None
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
    internal_sku: str
    platform_name: str
    action_type: TaskActionType
    priority: int
    task_status: TaskStatus
    created_at: datetime
    target_price: Decimal | None = None
    target_status: str | None = None
    pricing_source: PricingSource | None = None
    decision_trace: dict[str, Any] = field(default_factory=dict)
    result_message: str = ""

    def to_record(self) -> dict[str, Any]:
        data = asdict(self)
        data["action_type"] = self.action_type.value
        data["task_status"] = self.task_status.value
        data["pricing_source"] = self.pricing_source.value if self.pricing_source else None
        data["created_at"] = self.created_at.isoformat()
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


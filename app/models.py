from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.enums import (
    ListingStrategy,
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
    variety_filter: str
    grade_filter: str
    platform_filter: str
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
    variety_filter: str
    grade_filter: str
    platform_filter: str
    stock_threshold: Decimal
    listing_strategy: ListingStrategy
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
    confirmed_packing_capacity_qty_override: int | None = None
    allocation_rule: str = "proportional_by_forecast"
    active: bool = True
    listing_quota: dict[str, int] = field(default_factory=dict)
    note: str = ""

    @property
    def confirmed_temp_labor_capacity_qty(self) -> int:
        return self.confirmed_temp_worker_count * self.temp_worker_capacity_qty

    @property
    def confirmed_packing_capacity_qty(self) -> int:
        if self.confirmed_packing_capacity_qty_override is not None:
            return self.confirmed_packing_capacity_qty_override
        return self.normal_packing_capacity_qty + self.confirmed_temp_labor_capacity_qty


@dataclass(slots=True)
class ColdStorageStatus:
    trade_date: date
    total_capacity_qty: int = 500
    current_occupied_qty: int = 0
    expected_inbound_qty: int = 0
    expected_outbound_qty: int = 0
    warning_threshold_qty: int = 50
    projected_occupied_qty_override: int | None = None
    remaining_capacity_qty_override: int | None = None
    active: bool = True
    note: str = ""

    @property
    def projected_occupied_qty(self) -> int:
        if self.projected_occupied_qty_override is not None:
            return self.projected_occupied_qty_override
        return self.current_occupied_qty + self.expected_inbound_qty - self.expected_outbound_qty

    @property
    def remaining_capacity_qty(self) -> int:
        if self.remaining_capacity_qty_override is not None:
            return self.remaining_capacity_qty_override
        return self.total_capacity_qty - self.projected_occupied_qty

    @property
    def cold_storage_total_capacity_qty(self) -> int:
        return self.total_capacity_qty

    @property
    def cold_storage_current_qty(self) -> int:
        return self.projected_occupied_qty

    @property
    def cold_storage_available_capacity(self) -> int:
        return self.remaining_capacity_qty


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
    old_price: Decimal | None = None


@dataclass(slots=True)
class ListingStatus:
    listing_status_id: str
    platform_name: str
    internal_sku: str
    variety: str
    current_price: Decimal
    grade: str = ""
    platform_stock_qty: int = 100
    sold_qty: int = 0
    online_status: str = "online"
    source: str = "manual"
    updated_at: datetime | None = None
    inventory_source: str = "default"
    inventory_observed_at: datetime | None = None
    inventory_source_attempt_id: str = ""


@dataclass(slots=True)
class RulePricingResult:
    matched_rule_ids: list[str]
    matched_rule_names: list[str]
    old_price: Decimal | None
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
    expected_old_price: Decimal | None
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
    expected_old_price: Decimal | None = None
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
class ShadowBotOperationLedger:
    operation_id: str
    task_id: str
    platform: str
    product_identity: dict[str, Any]
    expected_old_price: Decimal
    target_price: Decimal
    status: str
    lock_owner: str = ""
    approved_payload_hash: str = ""
    write_identity_key: str = ""
    page_identity_key: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class ShadowBotExecutionAttempt:
    execution_attempt_id: str
    operation_id: str
    execution_mode: str
    shadowbot_run_id: str
    status: str
    side_effect_state: str
    started_at: datetime
    instruction_hash: str = ""
    request_file_sha256: str = ""
    queue_request_path: str = ""
    ended_at: datetime | None = None
    raw_output: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ShadowBotBatch:
    batch_id: str
    contract_version: int
    platform: str
    batch_type: str
    execution_mode: str
    identity_normalization_version: str
    normalized_request_digest: str
    stop_policy: str
    source_read_batch_id: str
    source_snapshot_sha256: str
    source_page_context_sha256: str
    source_observed_at: datetime
    source_snapshot_max_age_seconds: int
    status: str
    created_by: str
    capture_evidence: bool = False
    current_item_id: str = ""
    pending_count: int = 0
    ready_count: int = 0
    running_count: int = 0
    processed_count: int = 0
    previewed_count: int = 0
    verified_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    cancelled_count: int = 0
    needs_reconciliation_count: int = 0
    reconciled_item_count: int = 0
    paused_reason: str = ""
    error_code: str = ""
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class ShadowBotBatchItem:
    batch_id: str
    item_id: str
    ordinal: int
    source_item_id: str
    source_read_batch_id: str
    source_snapshot_sha256: str
    source_page_context_sha256: str
    task_id: str
    review_task_id: str
    operation_id: str
    approved_payload_hash: str
    page_identity_key: str
    write_identity_key: str
    expected_product_name: str
    expected_grade: str
    approved_expected_old_price: Decimal
    target_price: Decimal
    status: str
    external_platform_sku: str | None = None
    current_execution_attempt_id: str = ""
    current_run_id: str = ""
    fresh_read_attempt_id: str = ""
    fresh_read_result_sha256: str = ""
    fresh_old_price: Decimal | None = None
    post_commit_price: Decimal | None = None
    reconcile_attempt_id: str = ""
    reconciliation_outcome: str = ""
    reconciled_at: datetime | None = None
    error_code: str = ""
    error_message: str = ""
    result_id: str = ""
    result_hash: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class ShadowBotSideEffectCheckpoint:
    operation_id: str
    execution_attempt_id: str
    side_effect_state: str
    checkpoint_at: datetime
    version: int


@dataclass(slots=True)
class RetryAuthorization:
    """Persisted authorization envelope for a retry attempt.

    Task 5 owns the storage contract only.  Issuance and consumption semantics
    remain in the retry service delivered by the dependent task.
    """

    retry_authorization_id: str
    operation_id: str
    source_execution_attempt_id: str
    authorization_type: str
    authorized_by: str
    evidence_type: str
    evidence_hash: str
    approved_payload_hash: str
    status: str
    max_uses: int = 1
    consumed_by_execution_attempt_id: str | None = None
    expires_at: datetime | None = None
    reason: str = ""
    created_at: datetime | None = None
    consumed_at: datetime | None = None


@dataclass(slots=True)
class MockPlatformProductState:
    platform_name: str
    internal_sku: str
    platform_sku: str
    product_name: str
    grade: str
    platform_price: Decimal | None
    platform_online_status: str
    platform_stock_qty: int
    last_synced_at: datetime | None = None
    last_platform_update_at: datetime | None = None
    last_error: str = ""


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
class ReviewToken:
    token_id: str
    review_task_id: str
    token_hash: str
    token_subject: str
    allowed_actions: list[str]
    expires_at: datetime
    used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime | None = None
    created_by: str = "system"
    last_used_at: datetime | None = None
    note: str | None = None


@dataclass(slots=True)
class MobileReviewAtomicResult:
    """Committed state returned by the single-transaction Mobile Review flow."""

    review_task: ReviewTask
    review_token: ReviewToken
    source_task: Task | None
    source_task_status: TaskStatus | None


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
class NotificationOutbox:
    """Durable notification intent and its fenced delivery lease."""

    notification_id: str
    notification_key: str
    notification_type: str
    recipient_type: str
    recipient_ref: str
    channel: str
    priority: int
    payload: dict[str, Any] = field(default_factory=dict)
    related_task_id: str | None = None
    related_review_task_id: str | None = None
    status: str = "PENDING"
    attempt_count: int = 0
    max_attempts: int = 3
    next_attempt_at: datetime | None = None
    deadline_at: datetime | None = None
    lease_owner_token: str = ""
    lease_version: int = 0
    lease_expires_at: datetime | None = None
    sent_at: datetime | None = None
    provider_message_id: str = ""
    last_error_code: str = ""
    last_error_message: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def recipient(self) -> str:
        """Compatibility alias for the v5 notification log vocabulary."""

        return self.recipient_ref


@dataclass(slots=True)
class NotificationDeliveryAttempt:
    """One provider call intent/result, fenced by notification lease version."""

    delivery_attempt_id: str
    notification_id: str
    attempt_no: int
    status: str
    lease_owner_token: str
    lease_version: int
    request_fingerprint: str
    started_at: datetime
    completed_at: datetime | None = None
    provider_status_code: str = ""
    provider_message_id: str = ""
    response_fingerprint: str = ""
    error_code: str = ""
    error_message: str = ""


@dataclass(slots=True)
class NotificationDeliveryResult:
    """Provider-neutral, bounded result returned by a sender."""

    classification: str
    provider_status_code: str = ""
    provider_message_id: str = ""
    retry_after_seconds: int | None = None
    error_code: str = ""
    error_message: str = ""
    response_fingerprint: str = ""


@dataclass(slots=True)
class ScriptRun:
    script_run_id: str
    evaluator_id: str
    evaluator_name: str
    description: str
    run_mode: str
    run_status: str
    trade_date: date | None
    started_at: datetime
    finished_at: datetime | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""
    created_by: str = "system"


@dataclass(slots=True)
class ScriptRunItem:
    item_id: str
    script_run_id: str
    proposal_type: str
    dedupe_key: str
    severity: str
    item_status: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    decision_trace: dict[str, Any] = field(default_factory=dict)
    related_task_id: str | None = None
    related_review_task_id: str | None = None
    related_notification_id: str | None = None
    error_message: str = ""
    created_at: datetime | None = None


@dataclass(slots=True)
class NotificationSendResult:
    send_status: str
    sent_at: datetime | None
    error_message: str = ""
    provider_message_id: str = ""
    raw_response_json: dict[str, Any] = field(default_factory=dict)


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

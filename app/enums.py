from enum import Enum


class PricingMethod(str, Enum):
    FIXED_MARKUP = "fixed_markup"
    PERCENTAGE_MARKUP = "percentage_markup"


class RoundingRule(str, Enum):
    NONE = "none"
    ROUND = "round"
    CEIL = "ceil"
    FLOOR = "floor"
    STEP = "step"


class ConditionType(str, Enum):
    STOCK_LTE = "stock_lte"
    STOCK_GTE = "stock_gte"
    SALE_DISABLED = "sale_disabled"
    TIME_GTE = "time_gte"


class ListingAction(str, Enum):
    SET_ONLINE = "set_online"
    SET_OFFLINE = "set_offline"


class ListingStrategy(str, Enum):
    ALLOW_ONLINE = "allow_online"
    PROHIBIT_ONLINE = "prohibit_online"
    STOCK_BELOW_OFFLINE = "stock_below_offline"
    STOCK_ABOVE_ONLINE = "stock_above_online"


class TaskActionType(str, Enum):
    UPDATE_PRICE = "update_price"
    SET_ONLINE = "set_online"
    SET_OFFLINE = "set_offline"
    SYNC_STATUS = "sync_status"
    CAPACITY_WARNING = "capacity_warning"
    LABOR_REQUIRED = "labor_required"
    MANUAL_PRICE_REVIEW = "manual_price_review"
    BELOW_BREAK_EVEN_REVIEW = "below_break_even_review"
    SHORTAGE_WARNING = "shortage_warning"
    COLD_STORAGE_WARNING = "cold_storage_warning"
    CLEARANCE_WARNING = "clearance_warning"
    MANUAL_REVIEW = "manual_review"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    MANUAL_REVIEW = "manual_review"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ReviewTaskStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ADJUSTED = "adjusted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class NotificationSendStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


class PricingSource(str, Enum):
    RULE_ONLY = "rule_only"
    AI_SUGGESTED = "ai_suggested"
    RULE_PLUS_AI = "rule_plus_ai"
    MANUAL_OVERRIDE = "manual_override"
    FORECAST_PRICE = "forecast_price"
    RULE_PLUS_FORECAST = "rule_plus_forecast"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class TradePhase(str, Enum):
    NORMAL_TRADING = "normal_trading"
    CLEARANCE = "clearance"
    CLOSED = "closed"


class ShortageRisk(str, Enum):
    LOW = "low"
    MANAGEABLE = "manageable"
    HIGH = "high"

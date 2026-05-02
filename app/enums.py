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


class ListingAction(str, Enum):
    SET_ONLINE = "set_online"
    SET_OFFLINE = "set_offline"


class TaskActionType(str, Enum):
    UPDATE_PRICE = "update_price"
    SET_ONLINE = "set_online"
    SET_OFFLINE = "set_offline"
    SYNC_STATUS = "sync_status"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    MANUAL_REVIEW = "manual_review"
    CANCELLED = "cancelled"


class PricingSource(str, Enum):
    RULE_ONLY = "rule_only"
    AI_SUGGESTED = "ai_suggested"
    RULE_PLUS_AI = "rule_plus_ai"
    MANUAL_OVERRIDE = "manual_override"


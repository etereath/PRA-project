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
    SET_OFFLINE = "set_offline"
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


class NotificationOutboxStatus(str, Enum):
    """Durable logical-notification state, independent from provider results."""

    PENDING = "PENDING"
    LEASED = "LEASED"
    SENDING = "SENDING"
    RETRY_WAIT = "RETRY_WAIT"
    SENT = "SENT"
    UNKNOWN_DELIVERY = "UNKNOWN_DELIVERY"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class DeliveryAttemptStatus(str, Enum):
    STARTED = "STARTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    TEMP_FAILED = "TEMP_FAILED"
    PERM_FAILED = "PERM_FAILED"
    UNKNOWN = "UNKNOWN"


class DeliveryClassification(str, Enum):
    """Safe result classes returned by a notification sender."""

    SUCCESS = "SUCCESS"
    TEMP_FAILED = "TEMP_FAILED"
    PERM_FAILED = "PERM_FAILED"
    UNKNOWN = "UNKNOWN"


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


class SellerPhase(str, Enum):
    """Operational phase on the seller's 20:00-to-20:00 work day."""

    NORMAL_SALES = "NORMAL_SALES"
    PEAK_SALES = "PEAK_SALES"
    DELIVERY_OVERLAP = "DELIVERY_OVERLAP"


class FactSource(str, Enum):
    """How an accepted sales fact was obtained."""

    ORDER_OBSERVED = "ORDER_OBSERVED"
    SCAN_ESTIMATED = "SCAN_ESTIMATED"


class DataQualityLevel(str, Enum):
    """Frozen six-level sales data quality vocabulary."""

    ORDER_COMPLETE = "ORDER_COMPLETE"
    ORDER_PARTIAL = "ORDER_PARTIAL"
    SCAN_ESTIMATED_HIGH = "SCAN_ESTIMATED_HIGH"
    SCAN_ESTIMATED_MEDIUM = "SCAN_ESTIMATED_MEDIUM"
    SCAN_ESTIMATED_LOW = "SCAN_ESTIMATED_LOW"
    UNAVAILABLE = "UNAVAILABLE"


class SummaryStatus(str, Enum):
    """Single forward-only platform trade-day summary lifecycle."""

    PROVISIONAL = "PROVISIONAL"
    OBSERVED = "OBSERVED"
    RECONCILED = "RECONCILED"
    FINAL = "FINAL"


class TaskOriginType(str, Enum):
    """Origin boundary for manual, automation, and future emergency tasks."""

    MANUAL = "MANUAL"
    AUTOMATION = "AUTOMATION"
    SYSTEM_EMERGENCY = "SYSTEM_EMERGENCY"
    LEGACY = "LEGACY"


class AutomationRunStatus(str, Enum):
    """Canonical automation run states shared by storage and operations UI."""

    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    MISSED = "MISSED"
    MERGED = "MERGED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class IncidentCategory(str, Enum):
    """Stable operational incident categories frozen by task 13.5."""

    PLATFORM_LOGIN = "PLATFORM_LOGIN"
    PLATFORM_NETWORK = "PLATFORM_NETWORK"
    PAGE_STRUCTURE = "PAGE_STRUCTURE"
    SCAN_INCOMPLETE = "SCAN_INCOMPLETE"
    WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"
    QUEUE_BACKLOG = "QUEUE_BACKLOG"
    PRODUCT_MAPPING = "PRODUCT_MAPPING"
    PRICE_ANOMALY = "PRICE_ANOMALY"
    INVENTORY_ANOMALY = "INVENTORY_ANOMALY"
    ORDER_PAGE_UNAVAILABLE = "ORDER_PAGE_UNAVAILABLE"
    ORDER_DATA_INCONSISTENT = "ORDER_DATA_INCONSISTENT"
    SALES_ESTIMATE_LOW_CONFIDENCE = "SALES_ESTIMATE_LOW_CONFIDENCE"
    NOTIFICATION_FAILURE = "NOTIFICATION_FAILURE"
    WRITE_UNKNOWN = "WRITE_UNKNOWN"


class IncidentStatus(str, Enum):
    """Frozen lifecycle for operational incident handling."""

    OPEN = "OPEN"
    RETRYING = "RETRYING"
    WAITING_HUMAN = "WAITING_HUMAN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    AUTO_PROTECTING = "AUTO_PROTECTING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class ShortageRisk(str, Enum):
    LOW = "low"
    MANAGEABLE = "manageable"
    HIGH = "high"

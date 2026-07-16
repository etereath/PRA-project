from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(slots=True)
class TableValidationIssue:
    row_number: int
    field_name: str
    message: str


class ValidationError(Exception):
    """Raised when workbook input or business data is invalid."""


class MobileReviewErrorCode(str, Enum):
    """Stable business error codes for the one-time mobile review transaction."""

    TOKEN_NOT_FOUND = "TOKEN_NOT_FOUND"
    TOKEN_REVIEW_MISMATCH = "TOKEN_REVIEW_MISMATCH"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_REVOKED = "TOKEN_REVOKED"
    TOKEN_ALREADY_USED = "TOKEN_ALREADY_USED"
    REVIEW_NOT_FOUND = "REVIEW_NOT_FOUND"
    REVIEW_ALREADY_RESOLVED = "REVIEW_ALREADY_RESOLVED"
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"
    ACTION_NOT_ALLOWED_FOR_REVIEW_TYPE = "ACTION_NOT_ALLOWED_FOR_REVIEW_TYPE"
    INVALID_ADJUSTMENT = "INVALID_ADJUSTMENT"
    SOURCE_TASK_NOT_FOUND = "SOURCE_TASK_NOT_FOUND"
    CONCURRENT_UPDATE = "CONCURRENT_UPDATE"


class MobileReviewTransactionError(ValidationError):
    """A stable, user-safe business failure from the atomic Mobile Review flow."""

    def __init__(self, code: MobileReviewErrorCode, message: str):
        self.code = code.value
        self.code_enum = code
        super().__init__(message)


class TableValidationError(ValidationError):
    """Raised when editable table content contains field-level validation issues."""

    def __init__(self, table_name: str, issues: list[TableValidationIssue]):
        self.table_name = table_name
        self.issues = issues
        summary = f"{table_name}: {len(issues)} validation issue(s)"
        super().__init__(summary)


class AIProviderError(Exception):
    """Raised when an AI suggestion provider fails."""


class NotificationOutboxError(Exception):
    """Base error for durable notification workflow failures."""


class NotificationLeaseError(NotificationOutboxError):
    """The notification lease is missing, expired, or fenced by another worker."""


class NotificationDeliveryError(NotificationOutboxError):
    """The delivery attempt cannot be completed under its current lease."""

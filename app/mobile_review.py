from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.enums import ReviewTaskStatus
from app.exceptions import MobileReviewErrorCode, MobileReviewTransactionError


ADJUSTMENT_FIELDS = frozenset({"target_price", "target_status", "result_message"})


def normalize_mobile_review_resolution_payload(
    status: ReviewTaskStatus,
    payload: dict[str, object] | None,
) -> dict[str, object]:
    """Validate and normalize the side-effect-free Mobile Review payload."""

    normalized = dict(payload or {})
    if status != ReviewTaskStatus.ADJUSTED:
        return normalized

    adjustment = normalized.get("adjustment")
    if not isinstance(adjustment, dict):
        raise MobileReviewTransactionError(
            MobileReviewErrorCode.INVALID_ADJUSTMENT,
            "adjusted action requires an adjustment object",
        )
    unknown_fields = set(adjustment) - ADJUSTMENT_FIELDS
    if unknown_fields:
        raise MobileReviewTransactionError(
            MobileReviewErrorCode.INVALID_ADJUSTMENT,
            "adjustment contains unsupported fields",
        )
    if not {"target_price", "target_status"}.intersection(adjustment):
        raise MobileReviewTransactionError(
            MobileReviewErrorCode.INVALID_ADJUSTMENT,
            "adjustment must change target_price or target_status",
        )

    normalized_adjustment: dict[str, object] = {}
    if "target_price" in adjustment:
        normalized_adjustment["target_price"] = _normalize_target_price(adjustment["target_price"])
    if "target_status" in adjustment:
        normalized_adjustment["target_status"] = _normalize_text(
            adjustment["target_status"],
            field_name="target_status",
            max_length=100,
        )
    if "result_message" in adjustment:
        normalized_adjustment["result_message"] = _normalize_text(
            adjustment["result_message"],
            field_name="result_message",
            max_length=4000,
        )
    normalized["adjustment"] = normalized_adjustment
    return normalized


def _normalize_target_price(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise MobileReviewTransactionError(
            MobileReviewErrorCode.INVALID_ADJUSTMENT,
            "target_price must be a non-negative decimal",
        )
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise MobileReviewTransactionError(
            MobileReviewErrorCode.INVALID_ADJUSTMENT,
            "target_price must be a non-negative decimal",
        ) from None
    if not decimal_value.is_finite() or decimal_value < 0:
        raise MobileReviewTransactionError(
            MobileReviewErrorCode.INVALID_ADJUSTMENT,
            "target_price must be a non-negative decimal",
        )
    return format(decimal_value.normalize(), "f")


def _normalize_text(value: Any, *, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise MobileReviewTransactionError(
            MobileReviewErrorCode.INVALID_ADJUSTMENT,
            f"{field_name} must be text",
        )
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise MobileReviewTransactionError(
            MobileReviewErrorCode.INVALID_ADJUSTMENT,
            f"{field_name} is empty or too long",
        )
    return normalized

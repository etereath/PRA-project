"""Stable HTTP mapping shared by legacy and Operations Mobile Review routes."""

from __future__ import annotations

from app.exceptions import MobileReviewErrorCode


MOBILE_REVIEW_HTTP_STATUS = {
    MobileReviewErrorCode.TOKEN_NOT_FOUND.value: "403 Forbidden",
    MobileReviewErrorCode.TOKEN_REVIEW_MISMATCH.value: "403 Forbidden",
    MobileReviewErrorCode.TOKEN_EXPIRED.value: "410 Gone",
    MobileReviewErrorCode.TOKEN_REVOKED.value: "410 Gone",
    MobileReviewErrorCode.TOKEN_ALREADY_USED.value: "410 Gone",
    MobileReviewErrorCode.REVIEW_NOT_FOUND.value: "404 Not Found",
    MobileReviewErrorCode.SOURCE_TASK_NOT_FOUND.value: "404 Not Found",
    MobileReviewErrorCode.REVIEW_ALREADY_RESOLVED.value: "409 Conflict",
    MobileReviewErrorCode.ACTION_NOT_ALLOWED.value: "403 Forbidden",
    MobileReviewErrorCode.ACTION_NOT_ALLOWED_FOR_REVIEW_TYPE.value: (
        "422 Unprocessable Entity"
    ),
    MobileReviewErrorCode.INVALID_ADJUSTMENT.value: "422 Unprocessable Entity",
    MobileReviewErrorCode.CONCURRENT_UPDATE.value: "409 Conflict",
}


def mobile_review_http_status(error_code: str) -> str:
    return MOBILE_REVIEW_HTTP_STATUS.get(str(error_code), "409 Conflict")

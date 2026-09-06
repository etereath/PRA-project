from __future__ import annotations

import pytest

from app.exceptions import MobileReviewErrorCode
from app.mobile_review_http import mobile_review_http_status


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (MobileReviewErrorCode.TOKEN_NOT_FOUND.value, "403 Forbidden"),
        (MobileReviewErrorCode.TOKEN_REVIEW_MISMATCH.value, "403 Forbidden"),
        (MobileReviewErrorCode.TOKEN_EXPIRED.value, "410 Gone"),
        (MobileReviewErrorCode.TOKEN_REVOKED.value, "410 Gone"),
        (MobileReviewErrorCode.TOKEN_ALREADY_USED.value, "410 Gone"),
        (MobileReviewErrorCode.REVIEW_NOT_FOUND.value, "404 Not Found"),
        (MobileReviewErrorCode.SOURCE_TASK_NOT_FOUND.value, "404 Not Found"),
        (MobileReviewErrorCode.REVIEW_ALREADY_RESOLVED.value, "409 Conflict"),
        (MobileReviewErrorCode.ACTION_NOT_ALLOWED.value, "403 Forbidden"),
        (
            MobileReviewErrorCode.ACTION_NOT_ALLOWED_FOR_REVIEW_TYPE.value,
            "422 Unprocessable Entity",
        ),
        (
            MobileReviewErrorCode.INVALID_ADJUSTMENT.value,
            "422 Unprocessable Entity",
        ),
        (MobileReviewErrorCode.CONCURRENT_UPDATE.value, "409 Conflict"),
    ],
)
def test_mobile_review_http_mapping_is_shared_and_stable(code, expected):
    assert mobile_review_http_status(code) == expected


def test_unknown_mobile_review_error_fails_closed_as_conflict():
    assert mobile_review_http_status("UNKNOWN_SYNTHETIC_CODE") == "409 Conflict"

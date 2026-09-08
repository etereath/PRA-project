from datetime import datetime, timezone

import pytest

from app.enums import ReviewTaskStatus
from app.models import ReviewTask
from app.review_display import (
    review_reason_display,
    review_scope_display,
    review_type_display_label,
)


NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


def _review(
    *,
    review_type: str = "manual_review",
    reason: str = "",
    scope_type: str = "sku",
    scope_key: str = "AISHA-A-50-Z",
    internal_sku: str | None = "AISHA-A-50-Z",
    payload: dict | None = None,
) -> ReviewTask:
    return ReviewTask(
        review_task_id="REVIEW-DISPLAY",
        trade_date=NOW.date(),
        scope_type=scope_type,
        scope_key=scope_key,
        dedupe_key="review-display",
        source_task_id=None,
        review_type=review_type,
        review_status=ReviewTaskStatus.PENDING,
        internal_sku=internal_sku,
        platform_name="蚂蚁花团供应商",
        reason=reason,
        review_payload=payload or {},
        required_by=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.parametrize(
    ("review_type", "expected"),
    [
        ("manual_review", "执行结果确认"),
        ("manual_price_review", "价格确认"),
        ("below_break_even_review", "低于基础成本确认"),
        ("shadowbot_login_verification", "登录验证"),
        ("listing_location_anomaly", "商品资料确认"),
        ("unknown_internal", "人工确认"),
    ],
)
def test_review_type_labels_are_operator_facing(review_type: str, expected: str) -> None:
    assert review_type_display_label(review_type) == expected


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("missing_recommended_price", "目前没有可用的建议售价，请填写目标价格后再处理。"),
        ("below_absolute_min_price", "建议售价低于最低安全价，请确认目标价格。"),
        ("below_break_even_price", "建议售价低于商品基础成本，请确认是否继续。"),
        ("high_shortage_risk", "预计库存不足，继续销售可能影响履约，请确认处理方式。"),
        ("ShadowBot FAILED", "执行端未能完成任务，请选择重试或取消。"),
        ("LOGIN_STATE_UNKNOWN", "执行端未能完成任务，请选择重试或取消。"),
        ("needs manual review", "请查看当前情况并选择处理方式。"),
    ],
)
def test_reason_codes_are_translated(reason: str, expected: str) -> None:
    assert review_reason_display(_review(reason=reason)) == expected


def test_login_verification_hides_legacy_english_reason() -> None:
    review = _review(
        review_type="shadowbot_login_verification",
        reason="ShadowBot is waiting for manual phone verification in the desktop mini program.",
        scope_type="task",
        scope_key="ATTEMPT-INTERNAL",
    )

    assert review_reason_display(review) == (
        "登录需要手机验证码，请在手机端完成验证后点击“处理完毕”。"
    )
    assert review_scope_display(review) == "蚂蚁花团供应商 · 登录验证"


def test_listing_anomaly_replaces_sku_and_page_identity_terms() -> None:
    assert review_reason_display(
        _review(
            review_type="listing_location_anomaly",
            reason="页面商品未映射到库存 SKU：艾莎 A级",
        )
    ) == "平台商品尚未关联到系统商品：艾莎 A级"


def test_group_scope_never_displays_internal_group_id() -> None:
    review = _review(
        scope_type="task_group",
        scope_key="MANUAL-GROUP-INTERNAL",
        internal_sku=None,
        payload={
            "task_group_id": "MANUAL-GROUP-INTERNAL",
            "affected_task_count": 2,
        },
    )

    scope = review_scope_display(review)

    assert scope == "蚂蚁花团供应商 · 2 个商品"
    assert "MANUAL-GROUP" not in scope

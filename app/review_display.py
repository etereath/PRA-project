"""Operator-facing labels for Review and notification presentation.

Stored reasons and scope identifiers remain immutable audit evidence.  This
module translates them only at presentation and notification boundaries.
"""

from __future__ import annotations

import re

from app.enums import TaskActionType
from app.models import ReviewTask
from app.review_policy import review_task_group_id


REVIEW_TYPE_DISPLAY_LABELS = {
    "capacity_warning": "包装产能确认",
    "labor_required": "临时用工确认",
    "shortage_warning": "库存不足确认",
    "cold_storage_warning": "冷库容量确认",
    "clearance_warning": "清库存确认",
    "manual_price_review": "价格确认",
    "below_break_even_review": "低于基础成本确认",
    "manual_review": "执行结果确认",
    "emergency_protection": "价格异常处理",
    "shadowbot_login_verification": "登录验证",
    "listing_location_anomaly": "商品资料确认",
}

_REASON_CODE_LABELS = {
    "missing_recommended_price": "目前没有可用的建议售价，请填写目标价格后再处理。",
    "below_absolute_min_price": "建议售价低于最低安全价，请确认目标价格。",
    "below_break_even_price": "建议售价低于商品基础成本，请确认是否继续。",
    "high_shortage_risk": "预计库存不足，继续销售可能影响履约，请确认处理方式。",
}


def review_type_display_label(review_type: object) -> str:
    value = str(review_type or "").strip().lower()
    return REVIEW_TYPE_DISPLAY_LABELS.get(value, "人工确认")


def review_reason_display(review: ReviewTask) -> str:
    reason = str(review.reason or "").strip()
    lowered = reason.lower()
    if lowered in _REASON_CODE_LABELS:
        return _REASON_CODE_LABELS[lowered]
    if review.review_type == "shadowbot_login_verification" or (
        "waiting for manual phone verification" in lowered
    ):
        return "登录需要手机验证码，请在手机端完成验证后点击“处理完毕”。"
    if "只读 reconcile" in lowered or "只读对账仍无法确认" in reason:
        return _unknown_execution_reason(review)
    if reason.startswith("ShadowBot ") or _looks_like_error_code(reason):
        return "执行端未能完成任务，请选择重试或取消。"
    if _looks_like_english_internal_reason(reason):
        return _default_reason(review.review_type)
    if reason.startswith("任务组中有") and "等待人工复核" in reason:
        count = _affected_count(review)
        return (
            f"有 {count} 个商品需要确认执行结果。"
            if count > 0
            else "有商品需要确认执行结果。"
        )
    replacements = {
        "页面商品未映射到库存 SKU：": "平台商品尚未关联到系统商品：",
        "页面身份对应多个库存 SKU：": "平台商品同时关联到多个系统商品：",
        "商品页面身份不唯一：": "平台页面存在重复商品：",
    }
    for source, target in replacements.items():
        if reason.startswith(source):
            return target + reason[len(source) :]
    return reason or _default_reason(review.review_type)


def review_scope_display(
    review: ReviewTask,
    *,
    product_label: str = "",
    affected_product_labels: tuple[str, ...] = (),
) -> str:
    platform = str(review.platform_name or "").strip()
    if review_task_group_id(review):
        labels = tuple(dict.fromkeys(label for label in affected_product_labels if label))
        if labels:
            visible = "、".join(labels[:3])
            if len(labels) > 3:
                visible += f"等 {len(labels)} 个商品"
            scope = visible
        else:
            count = _affected_count(review)
            scope = f"{count} 个商品" if count > 0 else "相关商品"
        return " · ".join(part for part in (platform, scope) if part)
    if product_label:
        return " · ".join(part for part in (platform, product_label) if part)
    if review.review_type == "shadowbot_login_verification":
        return " · ".join(part for part in (platform, "登录验证") if part)
    if review.internal_sku:
        return " · ".join(part for part in (platform, "相关商品") if part)
    if review.scope_type == "incident":
        return " · ".join(part for part in (platform, "价格异常商品") if part)
    if review.scope_type == "platform_listing":
        return " · ".join(part for part in (platform, "平台商品") if part)
    labels = {
        "global": "全部业务",
        "platform": "全部商品",
        "trade_date": "当前销售日",
        "sku": "相关商品",
        "task": "相关任务",
    }
    scope = labels.get(str(review.scope_type).lower(), "相关业务")
    return " · ".join(part for part in (platform, scope) if part)


def _unknown_execution_reason(review: ReviewTask) -> str:
    payload = review.review_payload if isinstance(review.review_payload, dict) else {}
    action_types = {
        str(value).strip().lower()
        for value in payload.get("action_types", ())
        if str(value).strip()
    }
    single_action = str(payload.get("action_type") or "").strip().lower()
    if single_action:
        action_types.add(single_action)
    if action_types == {TaskActionType.SET_ONLINE.value}:
        action = "商品是否成功上架"
    elif action_types == {TaskActionType.SET_OFFLINE.value}:
        action = "商品是否成功下架"
    elif action_types == {TaskActionType.UPDATE_PRICE.value}:
        action = "商品价格是否修改成功"
    else:
        action = "任务是否执行成功"
    return f"自动核对后仍无法确认{action}，请在平台检查实际状态。"


def _affected_count(review: ReviewTask) -> int:
    payload = review.review_payload if isinstance(review.review_payload, dict) else {}
    try:
        count = int(payload.get("affected_task_count"))
    except (TypeError, ValueError):
        count = 0
    if count > 0:
        return count
    values = payload.get("affected_task_ids")
    return len(values) if isinstance(values, list) else 0


def _looks_like_error_code(reason: str) -> bool:
    return bool(reason and re.fullmatch(r"[A-Z][A-Z0-9_:-]{3,}", reason))


def _looks_like_english_internal_reason(reason: str) -> bool:
    letters = re.findall(r"[A-Za-z]", reason)
    return len(letters) >= 4 and not re.search(r"[\u4e00-\u9fff]", reason)


def _default_reason(review_type: str) -> str:
    return {
        "manual_price_review": "目标价格需要人工确认。",
        "below_break_even_review": "目标价格低于商品基础成本，请确认是否继续。",
        "shortage_warning": "库存可能不足，请确认处理方式。",
        "capacity_warning": "包装产能需要确认。",
        "labor_required": "临时用工数量需要确认。",
        "cold_storage_warning": "冷库容量需要确认。",
        "clearance_warning": "清库存方案需要确认。",
        "listing_location_anomaly": "平台商品资料需要确认。",
    }.get(review_type, "请查看当前情况并选择处理方式。")

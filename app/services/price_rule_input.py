from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from app.enums import PricingMethod, RoundingRule
from app.exceptions import TableValidationError, ValidationError
from app.repositories.workbook_repository import PRICE_RULE_HEADERS, load_table_records, save_table_records
from app.services.product_inventory_input import GRADE_OPTIONS, PLATFORM_OPTIONS, normalize_product_name


WILDCARD_FILTER = "*"
PRICING_METHOD_OPTIONS = [PricingMethod.FIXED_MARKUP.value, PricingMethod.PERCENTAGE_MARKUP.value]
ROUNDING_RULE_OPTIONS = [
    RoundingRule.NONE.value,
    RoundingRule.ROUND.value,
    RoundingRule.CEIL.value,
    RoundingRule.FLOOR.value,
    RoundingRule.STEP.value,
]


class PriceRuleInputError(ValidationError):
    """面向运营价格规则表单的业务校验错误。"""


@dataclass(slots=True)
class PriceRuleForm:
    rule_name: str
    variety_filter: str
    grade_filter: str
    platform_filter: str
    pricing_method: str
    markup_value: Decimal
    min_price: Decimal | None
    rounding_rule: str
    rounding_step: Decimal | None
    active: bool
    priority: int
    remark: str = ""
    rule_id: str = ""


@dataclass(slots=True)
class PriceRuleSaveResult:
    rows: list[dict[str, object]]
    message: str
    level: str = "success"
    updated_rule_id: str | None = None


def load_price_rule_input_rows(price_rules_path: Path) -> list[dict[str, object]]:
    if not price_rules_path.exists():
        return []
    return [_ensure_price_rule_defaults(row) for row in load_table_records("price_rules", price_rules_path)]


def save_price_rule_input_rows(price_rules_path: Path, rows: list[dict[str, object]]) -> None:
    save_table_records("price_rules", price_rules_path, [_ensure_price_rule_defaults(row) for row in rows])


def validate_price_rule_form(
    values: dict[str, str],
    *,
    existing_rows: list[dict[str, object]],
    is_edit: bool,
    allowed_varieties: list[str] | None = None,
    allowed_platforms: list[str] | None = None,
) -> PriceRuleForm:
    rule_id = str(values.get("rule_id", "") or "").strip()
    if is_edit and not rule_id:
        raise PriceRuleInputError("缺少价格规则编号，无法保存。")

    rule_name = str(values.get("rule_name", "") or "").strip()
    if not rule_name:
        raise PriceRuleInputError("请输入规则名称。")

    variety_filter = _normalize_variety_filter(values.get("variety_filter", WILDCARD_FILTER), allowed_varieties)
    grade_filter = _normalize_grade_filter(values.get("grade_filter", WILDCARD_FILTER))
    platform_filter = _normalize_platform_filter(values.get("platform_filter", WILDCARD_FILTER), allowed_platforms)
    pricing_method = _normalize_pricing_method(values.get("pricing_method", ""))
    markup_value = _parse_non_zero_decimal(values.get("markup_value", ""), "改价值")
    min_price = _parse_optional_non_negative_decimal(values.get("min_price", ""), "最低价")
    rounding_rule = _normalize_rounding_rule(values.get("rounding_rule", ""))
    rounding_step = _parse_optional_non_negative_decimal(values.get("rounding_step", ""), "取整步长")
    if rounding_rule == RoundingRule.STEP.value and (rounding_step is None or rounding_step <= 0):
        raise PriceRuleInputError("取整规则为按步长时，取整步长必须大于 0。")

    active = _parse_yes_no(values.get("active", "true"), "是否启用")
    priority = _parse_non_negative_int(values.get("priority", "10"), "优先级")
    remark = str(values.get("remark", "") or "").strip()

    if is_edit:
        if not any(str(row.get("rule_id") or "").strip() == rule_id for row in existing_rows):
            raise PriceRuleInputError("未找到要编辑的价格规则。")
    elif rule_id and any(str(row.get("rule_id") or "").strip() == rule_id for row in existing_rows):
        raise PriceRuleInputError("价格规则编号已存在，请检查规则列表。")

    return PriceRuleForm(
        rule_id=rule_id,
        rule_name=rule_name,
        variety_filter=variety_filter,
        grade_filter=grade_filter,
        platform_filter=platform_filter,
        pricing_method=pricing_method,
        markup_value=markup_value,
        min_price=min_price,
        rounding_rule=rounding_rule,
        rounding_step=rounding_step,
        active=active,
        priority=priority,
        remark=remark,
    )


def apply_price_rule_input(rows: list[dict[str, object]], form: PriceRuleForm) -> PriceRuleSaveResult:
    rows = [_ensure_price_rule_defaults(row) for row in rows]
    rule_id = form.rule_id or _generate_rule_id(form.rule_name, rows)
    rows.append(_form_to_row(form, rule_id))
    return PriceRuleSaveResult(
        rows=rows,
        updated_rule_id=rule_id,
        message="已新增价格规则。保存的是业务输入数据，若要影响任务中心，请重新生成运行态任务。",
    )


def apply_price_rule_edit(rows: list[dict[str, object]], form: PriceRuleForm) -> PriceRuleSaveResult:
    rows = [_ensure_price_rule_defaults(row) for row in rows]
    for index, row in enumerate(rows):
        if str(row.get("rule_id") or "").strip() == form.rule_id:
            rows[index] = _form_to_row(form, form.rule_id)
            return PriceRuleSaveResult(
                rows=rows,
                updated_rule_id=form.rule_id,
                message="价格规则已保存。若要影响任务中心，请重新生成运行态任务。",
            )
    raise PriceRuleInputError("未找到要编辑的价格规则。")


def persist_price_rule_rows(price_rules_path: Path, rows: list[dict[str, object]]) -> None:
    try:
        save_price_rule_input_rows(price_rules_path, rows)
    except TableValidationError as exc:
        first_issue = exc.issues[0] if exc.issues else None
        if first_issue is None:
            raise PriceRuleInputError("价格规则表校验失败，请检查输入内容。") from exc
        raise PriceRuleInputError(
            f"价格规则表校验失败：第 {first_issue.row_number} 行 {first_issue.field_name} {first_issue.message}"
        ) from exc


def active_display(value: object) -> str:
    try:
        return "是" if _parse_yes_no(value, "是否启用") else "否"
    except PriceRuleInputError:
        return str(value or "-")


def format_price_rule_number(value: object) -> str:
    if value in (None, ""):
        return "-"
    text = str(value).strip()
    try:
        numeric = Decimal(text)
    except InvalidOperation:
        return text
    if numeric == numeric.to_integral():
        return str(int(numeric))
    return format(numeric.normalize(), "f")


def _form_to_row(form: PriceRuleForm, rule_id: str) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "rule_name": form.rule_name,
        "variety_filter": form.variety_filter,
        "grade_filter": form.grade_filter,
        "platform_filter": form.platform_filter,
        "pricing_method": form.pricing_method,
        "markup_value": _format_decimal(form.markup_value),
        "min_price": "" if form.min_price is None else _format_decimal(form.min_price),
        "rounding_rule": form.rounding_rule,
        "rounding_step": "" if form.rounding_step is None else _format_decimal(form.rounding_step),
        "active": "True" if form.active else "False",
        "priority": str(form.priority),
        "remark": form.remark,
    }


def _ensure_price_rule_defaults(row: dict[str, object]) -> dict[str, object]:
    return {header: row.get(header, "") for header in PRICE_RULE_HEADERS}


def _normalize_variety_filter(value: object, allowed_varieties: list[str] | None) -> str:
    raw = str(value if value is not None else "").strip()
    if raw == WILDCARD_FILTER:
        return WILDCARD_FILTER
    product_name = normalize_product_name(raw)
    if not product_name:
        raise PriceRuleInputError("品种筛选不能为空；如不限制品种，请选择“*”。")
    allowed = {normalize_product_name(item) for item in (allowed_varieties or [])}
    if allowed and product_name not in allowed:
        raise PriceRuleInputError("请选择当前商品资料中已有的品种，或选择“*”。")
    return product_name


def _normalize_grade_filter(value: object) -> str:
    raw = str(value if value is not None else "").strip()
    if raw == WILDCARD_FILTER:
        return WILDCARD_FILTER
    normalized = raw.upper()
    if normalized not in GRADE_OPTIONS:
        raise PriceRuleInputError("等级筛选必须选择 * / A / B / C / D / E / 0。")
    return normalized


def _normalize_platform_filter(value: object, allowed_platforms: list[str] | None) -> str:
    raw = str(value if value is not None else "").strip()
    if raw == WILDCARD_FILTER:
        return WILDCARD_FILTER
    platforms = allowed_platforms or PLATFORM_OPTIONS
    if raw not in platforms:
        raise PriceRuleInputError("请选择有效的平台，或选择“*”。")
    return raw


def _normalize_pricing_method(value: object) -> str:
    normalized = str(value or "").strip()
    if normalized not in PRICING_METHOD_OPTIONS:
        raise PriceRuleInputError("请选择有效的价格类型。")
    return normalized


def _normalize_rounding_rule(value: object) -> str:
    normalized = str(value or "").strip()
    if normalized not in ROUNDING_RULE_OPTIONS:
        raise PriceRuleInputError("请选择有效的取整规则。")
    return normalized


def _parse_non_negative_decimal(value: object, field_label: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise PriceRuleInputError(f"{field_label}必须是数字。") from None
    if decimal_value < 0:
        raise PriceRuleInputError(f"{field_label}必须大于或等于 0。")
    return decimal_value


def _parse_non_zero_decimal(value: object, field_label: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise PriceRuleInputError(f"{field_label}必须是数字。") from None
    if decimal_value == 0:
        raise PriceRuleInputError(f"{field_label}不能为 0；输入正数表示涨价，输入负数表示降价。")
    return decimal_value


def _parse_optional_non_negative_decimal(value: object, field_label: str) -> Decimal | None:
    if value in ("", None):
        return None
    return _parse_non_negative_decimal(value, field_label)


def _parse_non_negative_int(value: object, field_label: str) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise PriceRuleInputError(f"{field_label}必须是整数。") from None
    if parsed < 0:
        raise PriceRuleInputError(f"{field_label}不得小于 0。")
    return parsed


def _parse_yes_no(value: object, field_label: str) -> bool:
    normalized = str(value if value is not None else "").strip().lower()
    if normalized in {"true", "1", "yes", "y", "是"}:
        return True
    if normalized in {"false", "0", "no", "n", "否"}:
        return False
    raise PriceRuleInputError(f"{field_label}必须选择是或否。")


def _format_decimal(value: Decimal) -> str:
    if value == value.to_integral():
        return str(int(value))
    return format(value.normalize(), "f")


def _generate_rule_id(rule_name: str, rows: list[dict[str, object]]) -> str:
    base = re.sub(r"[^A-Z0-9]+", "-", rule_name.upper()).strip("-") or "PRICE-RULE"
    base = f"PRICE-{base[:32]}"
    used = {str(row.get("rule_id") or "").strip() for row in rows}
    if base not in used:
        return base
    for _ in range(1000):
        candidate = f"{base}-{uuid4().hex[:6].upper()}"
        if candidate not in used:
            return candidate
    raise PriceRuleInputError("价格规则编号冲突过多，请先检查价格规则表。")

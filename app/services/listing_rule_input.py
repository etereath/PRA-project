from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from app.enums import ListingStrategy
from app.exceptions import TableValidationError, ValidationError
from app.repositories.workbook_repository import LISTING_RULE_HEADERS, load_table_records, save_table_records
from app.services.price_rule_input import WILDCARD_FILTER
from app.services.product_inventory_input import GRADE_OPTIONS, PLATFORM_OPTIONS, normalize_product_name


LISTING_STRATEGY_OPTIONS = [item.value for item in ListingStrategy]


class ListingRuleInputError(ValidationError):
    """面向运营上下架规则表单的业务校验错误。"""


@dataclass(slots=True)
class ListingRuleForm:
    rule_name: str
    variety_filter: str
    grade_filter: str
    platform_filter: str
    stock_threshold: Decimal
    listing_strategy: str
    active: bool
    priority: int
    remark: str = ""
    rule_id: str = ""


@dataclass(slots=True)
class ListingRuleSaveResult:
    rows: list[dict[str, object]]
    message: str
    level: str = "success"
    updated_rule_id: str | None = None


def load_listing_rule_input_rows(listing_rules_path: Path) -> list[dict[str, object]]:
    if not listing_rules_path.exists():
        return []
    return [_ensure_listing_rule_defaults(row) for row in load_table_records("listing_rules", listing_rules_path)]


def save_listing_rule_input_rows(listing_rules_path: Path, rows: list[dict[str, object]]) -> None:
    save_table_records("listing_rules", listing_rules_path, [_ensure_listing_rule_defaults(row) for row in rows])


def validate_listing_rule_form(
    values: dict[str, str],
    *,
    existing_rows: list[dict[str, object]],
    is_edit: bool,
    allowed_varieties: list[str] | None = None,
    allowed_platforms: list[str] | None = None,
) -> ListingRuleForm:
    rule_id = str(values.get("rule_id", "") or "").strip()
    if is_edit and not rule_id:
        raise ListingRuleInputError("缺少上下架规则编号，无法保存。")

    rule_name = str(values.get("rule_name", "") or "").strip()
    if not rule_name:
        raise ListingRuleInputError("请输入规则名称。")

    variety_filter = _normalize_variety_filter(values.get("variety_filter", WILDCARD_FILTER), allowed_varieties)
    grade_filter = _normalize_grade_filter(values.get("grade_filter", WILDCARD_FILTER))
    platform_filter = _normalize_platform_filter(values.get("platform_filter", WILDCARD_FILTER), allowed_platforms)
    stock_threshold = _parse_non_negative_decimal(values.get("stock_threshold", "0"), "库存阈值")
    listing_strategy = _normalize_listing_strategy(values.get("listing_strategy", ""))
    active = _parse_yes_no(values.get("active", "true"), "是否启用")
    priority = _parse_non_negative_int(values.get("priority", "10"), "优先级")
    remark = str(values.get("remark", "") or "").strip()

    if is_edit:
        if not any(str(row.get("rule_id") or "").strip() == rule_id for row in existing_rows):
            raise ListingRuleInputError("未找到要编辑的上下架规则。")
    elif rule_id and any(str(row.get("rule_id") or "").strip() == rule_id for row in existing_rows):
        raise ListingRuleInputError("上下架规则编号已存在，请检查规则列表。")

    return ListingRuleForm(
        rule_id=rule_id,
        rule_name=rule_name,
        variety_filter=variety_filter,
        grade_filter=grade_filter,
        platform_filter=platform_filter,
        stock_threshold=stock_threshold,
        listing_strategy=listing_strategy,
        active=active,
        priority=priority,
        remark=remark,
    )


def apply_listing_rule_input(rows: list[dict[str, object]], form: ListingRuleForm) -> ListingRuleSaveResult:
    rows = [_ensure_listing_rule_defaults(row) for row in rows]
    rule_id = form.rule_id or _generate_rule_id(form.rule_name, rows)
    rows.append(_form_to_row(form, rule_id))
    return ListingRuleSaveResult(
        rows=rows,
        updated_rule_id=rule_id,
        message="已新增上下架规则。保存的是业务输入数据，若要影响任务中心，请重新生成运行态任务或运行对应规则评估。",
    )


def apply_listing_rule_edit(rows: list[dict[str, object]], form: ListingRuleForm) -> ListingRuleSaveResult:
    rows = [_ensure_listing_rule_defaults(row) for row in rows]
    for index, row in enumerate(rows):
        if str(row.get("rule_id") or "").strip() == form.rule_id:
            rows[index] = _form_to_row(form, form.rule_id)
            return ListingRuleSaveResult(
                rows=rows,
                updated_rule_id=form.rule_id,
                message="上下架规则已保存。若要影响任务中心，请重新生成运行态任务或运行对应规则评估。",
            )
    raise ListingRuleInputError("未找到要编辑的上下架规则。")


def persist_listing_rule_rows(listing_rules_path: Path, rows: list[dict[str, object]]) -> None:
    try:
        save_listing_rule_input_rows(listing_rules_path, rows)
    except TableValidationError as exc:
        first_issue = exc.issues[0] if exc.issues else None
        if first_issue is None:
            raise ListingRuleInputError("上下架规则表校验失败，请检查输入内容。") from exc
        raise ListingRuleInputError(
            f"上下架规则表校验失败：第 {first_issue.row_number} 行 {first_issue.field_name} {first_issue.message}"
        ) from exc


def active_display(value: object) -> str:
    try:
        return "是" if _parse_yes_no(value, "是否启用") else "否"
    except ListingRuleInputError:
        return str(value or "-")


def format_listing_rule_number(value: object) -> str:
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


def format_listing_rule_scope(row: dict[str, object]) -> str:
    variety = str(row.get("variety_filter") or "*").strip() or "*"
    grade = str(row.get("grade_filter") or "*").strip().upper() or "*"
    platform = str(row.get("platform_filter") or "*").strip() or "*"
    if variety == "*" and grade == "*" and platform == "*":
        return "全部商品"
    variety_label = "全部品种" if variety == "*" else variety
    grade_label = "全部等级" if grade == "*" else f"{grade}级"
    platform_label = "全部平台" if platform == "*" else platform
    return f"{variety_label} / {grade_label} / {platform_label}"


def _form_to_row(form: ListingRuleForm, rule_id: str) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "rule_name": form.rule_name,
        "variety_filter": form.variety_filter,
        "grade_filter": form.grade_filter,
        "platform_filter": form.platform_filter,
        "stock_threshold": _format_decimal(form.stock_threshold),
        "listing_strategy": form.listing_strategy,
        "active": "True" if form.active else "False",
        "priority": str(form.priority),
        "remark": form.remark,
    }


def _ensure_listing_rule_defaults(row: dict[str, object]) -> dict[str, object]:
    return {header: row.get(header, "") for header in LISTING_RULE_HEADERS}


def _normalize_variety_filter(value: object, allowed_varieties: list[str] | None) -> str:
    raw = str(value if value is not None else "").strip()
    if raw == WILDCARD_FILTER:
        return WILDCARD_FILTER
    product_name = normalize_product_name(raw)
    if not product_name:
        raise ListingRuleInputError("品种筛选不能为空；如不限制品种，请选择“*”。")
    allowed = {normalize_product_name(item) for item in (allowed_varieties or [])}
    if allowed and product_name not in allowed:
        raise ListingRuleInputError("请选择当前商品资料中已有的品种，或选择“*”。")
    return product_name


def _normalize_grade_filter(value: object) -> str:
    raw = str(value if value is not None else "").strip()
    if raw == WILDCARD_FILTER:
        return WILDCARD_FILTER
    normalized = raw.upper()
    if normalized not in GRADE_OPTIONS:
        raise ListingRuleInputError("等级筛选必须选择 * / A / B / C / D / E / 0。")
    return normalized


def _normalize_platform_filter(value: object, allowed_platforms: list[str] | None) -> str:
    raw = str(value if value is not None else "").strip()
    if raw == WILDCARD_FILTER:
        return WILDCARD_FILTER
    platforms = allowed_platforms or PLATFORM_OPTIONS
    if raw not in platforms:
        raise ListingRuleInputError("请选择有效的平台，或选择“*”。")
    return raw


def _normalize_listing_strategy(value: object) -> str:
    normalized = str(value or "").strip()
    if normalized not in LISTING_STRATEGY_OPTIONS:
        raise ListingRuleInputError("请选择有效的规则策略。")
    return normalized


def _parse_non_negative_decimal(value: object, field_label: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise ListingRuleInputError(f"{field_label}必须是数字。") from None
    if decimal_value < 0:
        raise ListingRuleInputError(f"{field_label}必须大于或等于 0。")
    return decimal_value


def _parse_non_negative_int(value: object, field_label: str) -> int:
    try:
        number = int(str(value).strip())
    except (ValueError, AttributeError):
        raise ListingRuleInputError(f"{field_label}必须是整数。") from None
    if number < 0:
        raise ListingRuleInputError(f"{field_label}必须大于或等于 0。")
    return number


def _parse_yes_no(value: object, field_label: str) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if text in {"true", "1", "yes", "y", "是"}:
        return True
    if text in {"false", "0", "no", "n", "否"}:
        return False
    raise ListingRuleInputError(f"{field_label}请选择“是”或“否”。")


def _format_decimal(value: Decimal) -> str:
    if value == value.to_integral():
        return str(int(value))
    return format(value.normalize(), "f")


def _generate_rule_id(rule_name: str, rows: list[dict[str, object]]) -> str:
    code = "".join(ch for ch in rule_name.upper() if ch.isalnum())
    prefix = f"LIST-{code[:12]}" if code else "LIST"
    existing = {str(row.get("rule_id") or "").strip() for row in rows}
    candidate = prefix
    if candidate and candidate not in existing:
        return candidate
    for index in range(2, 1000):
        candidate = f"{prefix}-{index}"
        if candidate not in existing:
            return candidate
    return f"LIST-{uuid4().hex[:8]}"

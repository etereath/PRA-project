from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.exceptions import ValidationError
from app.repositories.workbook_repository import load_table_records, save_table_records
from app.utils import parse_bool, parse_date


DEFAULT_BASE_CAPACITY_QTY = 250
DEFAULT_TEMP_WORKER_CAPACITY_QTY = 100
DEFAULT_ALLOCATION_RULE = "proportional_by_forecast"


class CapacityPlanInputError(ValueError):
    pass


@dataclass(slots=True)
class CapacityPlanForm:
    trade_date: date
    normal_packing_capacity_qty: int
    temp_worker_capacity_qty: int
    confirmed_temp_worker_count: int
    confirmed_packing_capacity_qty: int
    active: bool
    note: str = ""
    allocation_rule: str = DEFAULT_ALLOCATION_RULE


@dataclass(slots=True)
class CapacityPlanSaveResult:
    rows: list[dict[str, object]]
    message: str
    level: str = "success"


def load_capacity_plan_input_rows(path: Path) -> list[dict[str, object]]:
    rows = load_table_records("capacity_plans", path)
    normalized: list[dict[str, object]] = []
    for row in rows:
        normalized_row = dict(row)
        normalized_row.setdefault("confirmed_packing_capacity_qty", _computed_capacity(normalized_row))
        normalized_row.setdefault("active", True)
        normalized.append(normalized_row)
    return normalized


def persist_capacity_plan_rows(path: Path, rows: list[dict[str, object]]) -> None:
    save_table_records("capacity_plans", path, [_row_for_save(row) for row in rows])


def validate_capacity_plan_form(form: dict[str, str], *, existing_rows: list[dict[str, object]], is_edit: bool) -> CapacityPlanForm:
    trade_date_value = str(form.get("trade_date") or "").strip()
    if not trade_date_value:
        raise CapacityPlanInputError("请选择业务日期。")
    try:
        trade_date = parse_date(trade_date_value, "trade_date")
    except ValidationError as exc:
        raise CapacityPlanInputError("业务日期格式不正确。") from exc

    normal_capacity = _parse_non_negative_int(
        form.get("normal_packing_capacity_qty"),
        "基础包装产能必须是大于或等于 0 的数字。",
        default=DEFAULT_BASE_CAPACITY_QTY,
    )
    worker_count = _parse_non_negative_int(
        form.get("confirmed_temp_worker_count"),
        "临时工人数必须是大于或等于 0 的整数。",
        default=0,
    )
    worker_capacity = _parse_non_negative_int(
        form.get("temp_worker_capacity_qty"),
        "单人临时工产能必须是大于或等于 0 的数字。",
        default=DEFAULT_TEMP_WORKER_CAPACITY_QTY,
    )
    confirmed_capacity_value = str(form.get("confirmed_packing_capacity_qty") or "").strip()
    if confirmed_capacity_value:
        confirmed_capacity = _parse_non_negative_int(
            confirmed_capacity_value,
            "确认包装能力必须是大于或等于 0 的数字。",
            default=normal_capacity + worker_count * worker_capacity,
        )
    else:
        confirmed_capacity = normal_capacity + worker_count * worker_capacity

    try:
        active = parse_bool(form.get("active", "true"), "active")
    except ValidationError as exc:
        raise CapacityPlanInputError("请选择是否启用。") from exc

    note = str(form.get("note") or "").strip()
    allocation_rule = str(form.get("allocation_rule") or DEFAULT_ALLOCATION_RULE).strip() or DEFAULT_ALLOCATION_RULE
    current_trade_date = str(form.get("current_trade_date") or "").strip()
    current_row_index = _parse_optional_row_index(form.get("current_row_index"))
    if active:
        for index, row in enumerate(existing_rows):
            row_trade_date = _date_key(row.get("trade_date"))
            if not row_trade_date:
                continue
            if is_edit and current_row_index is not None and index == current_row_index:
                continue
            if is_edit and current_row_index is None and row_trade_date == current_trade_date:
                continue
            if row_trade_date == trade_date.isoformat() and _row_is_active(row):
                raise CapacityPlanInputError("同一业务日期已经存在启用的包装产能计划，请先编辑或停用原计划。")

    return CapacityPlanForm(
        trade_date=trade_date,
        normal_packing_capacity_qty=normal_capacity,
        temp_worker_capacity_qty=worker_capacity,
        confirmed_temp_worker_count=worker_count,
        confirmed_packing_capacity_qty=confirmed_capacity,
        active=active,
        note=note,
        allocation_rule=allocation_rule,
    )


def apply_capacity_plan_input(rows: list[dict[str, object]], form: CapacityPlanForm) -> CapacityPlanSaveResult:
    new_rows = [dict(row) for row in rows]
    new_rows.append(_form_to_row(form))
    return CapacityPlanSaveResult(
        rows=new_rows,
        message="已新增包装产能计划。保存后如需影响脚本状态和复核，请运行对应自动规则评估。",
    )


def apply_capacity_plan_edit(
    rows: list[dict[str, object]],
    current_trade_date: str,
    form: CapacityPlanForm,
    current_row_index: int | None = None,
) -> CapacityPlanSaveResult:
    current_key = str(current_trade_date or "").strip()
    if current_row_index is None and not current_key:
        raise CapacityPlanInputError("缺少要编辑的业务日期。")
    new_rows: list[dict[str, object]] = []
    updated = False
    for index, row in enumerate(rows):
        should_update = index == current_row_index if current_row_index is not None else _date_key(row.get("trade_date")) == current_key
        if should_update and not updated:
            new_rows.append(_form_to_row(form))
            updated = True
        else:
            new_rows.append(dict(row))
    if not updated:
        raise CapacityPlanInputError("没有找到要编辑的包装产能计划。")
    return CapacityPlanSaveResult(
        rows=new_rows,
        message="已保存包装产能计划。保存后如需影响脚本状态和复核，请运行对应自动规则评估。",
    )


def active_display(value: object) -> str:
    try:
        return "是" if parse_bool(value, "active") else "否"
    except ValidationError:
        return "否"


def format_capacity_number(value: object) -> str:
    if value in (None, ""):
        return "-"
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def computed_capacity_from_row(row: dict[str, object]) -> int:
    value = row.get("confirmed_packing_capacity_qty")
    if value not in (None, ""):
        return _parse_non_negative_int(value, "确认包装能力必须是大于或等于 0 的数字。", default=0)
    return _computed_capacity(row)


def _form_to_row(form: CapacityPlanForm) -> dict[str, object]:
    return {
        "trade_date": form.trade_date.isoformat(),
        "normal_packing_capacity_qty": form.normal_packing_capacity_qty,
        "temp_worker_capacity_qty": form.temp_worker_capacity_qty,
        "confirmed_temp_worker_count": form.confirmed_temp_worker_count,
        "confirmed_packing_capacity_qty": form.confirmed_packing_capacity_qty,
        "allocation_rule": form.allocation_rule,
        "active": form.active,
        "note": form.note,
    }


def _row_for_save(row: dict[str, object]) -> dict[str, object]:
    saved = dict(row)
    if saved.get("confirmed_packing_capacity_qty") in (None, ""):
        saved["confirmed_packing_capacity_qty"] = _computed_capacity(saved)
    saved.setdefault("allocation_rule", DEFAULT_ALLOCATION_RULE)
    saved.setdefault("active", True)
    saved.setdefault("note", "")
    return saved


def _computed_capacity(row: dict[str, object]) -> int:
    normal_capacity = _parse_non_negative_int(
        row.get("normal_packing_capacity_qty"),
        "基础包装产能必须是大于或等于 0 的数字。",
        default=DEFAULT_BASE_CAPACITY_QTY,
    )
    worker_count = _parse_non_negative_int(
        row.get("confirmed_temp_worker_count"),
        "临时工人数必须是大于或等于 0 的整数。",
        default=0,
    )
    worker_capacity = _parse_non_negative_int(
        row.get("temp_worker_capacity_qty"),
        "单人临时工产能必须是大于或等于 0 的数字。",
        default=DEFAULT_TEMP_WORKER_CAPACITY_QTY,
    )
    return normal_capacity + worker_count * worker_capacity


def _parse_non_negative_int(value: object, message: str, *, default: int) -> int:
    if value in (None, ""):
        return default
    text = str(value).strip()
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise CapacityPlanInputError(message) from exc
    if number < 0 or number != number.to_integral_value():
        raise CapacityPlanInputError(message)
    return int(number)


def _row_is_active(row: dict[str, object]) -> bool:
    try:
        return parse_bool(row.get("active", True), "active")
    except ValidationError:
        return False


def _date_key(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return parse_date(text, "trade_date").isoformat()
    except ValidationError:
        return text


def _parse_optional_row_index(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = int(text)
    except ValueError:
        return None
    if number < 0:
        return None
    return number

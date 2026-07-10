from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.exceptions import ValidationError
from app.repositories.workbook_repository import load_table_records, save_table_records
from app.utils import parse_bool, parse_date


DEFAULT_TOTAL_CAPACITY_QTY = 500
DEFAULT_WARNING_THRESHOLD_QTY = 50


class ColdStorageInputError(ValueError):
    pass


@dataclass(slots=True)
class ColdStorageForm:
    trade_date: date
    total_capacity_qty: int
    current_occupied_qty: int
    expected_inbound_qty: int
    expected_outbound_qty: int
    warning_threshold_qty: int
    projected_occupied_qty: int
    remaining_capacity_qty: int
    active: bool
    note: str = ""


@dataclass(slots=True)
class ColdStorageSaveResult:
    rows: list[dict[str, object]]
    message: str
    level: str = "success"


def load_cold_storage_input_rows(path: Path) -> list[dict[str, object]]:
    rows = load_table_records("cold_storage_status", path)
    normalized: list[dict[str, object]] = []
    for row in rows:
        normalized_row = dict(row)
        normalized_row.setdefault("expected_inbound_qty", 0)
        normalized_row.setdefault("expected_outbound_qty", 0)
        normalized_row.setdefault("warning_threshold_qty", DEFAULT_WARNING_THRESHOLD_QTY)
        normalized_row.setdefault("active", True)
        normalized_row.setdefault("note", "")
        if normalized_row.get("projected_occupied_qty") in (None, ""):
            normalized_row["projected_occupied_qty"] = computed_projected_occupied_from_row(normalized_row)
        if normalized_row.get("remaining_capacity_qty") in (None, ""):
            normalized_row["remaining_capacity_qty"] = computed_remaining_capacity_from_row(normalized_row)
        normalized.append(normalized_row)
    return normalized


def persist_cold_storage_rows(path: Path, rows: list[dict[str, object]]) -> None:
    save_table_records("cold_storage_status", path, [_row_for_save(row) for row in rows])


def validate_cold_storage_form(
    form: dict[str, str],
    *,
    existing_rows: list[dict[str, object]],
    is_edit: bool,
) -> ColdStorageForm:
    trade_date_value = str(form.get("trade_date") or "").strip()
    if not trade_date_value:
        raise ColdStorageInputError("请选择业务日期。")
    try:
        trade_date = parse_date(trade_date_value, "trade_date")
    except ValidationError as exc:
        raise ColdStorageInputError("业务日期格式不正确。") from exc

    total_capacity = _parse_positive_int(
        form.get("total_capacity_qty"),
        "冷库总容量必须是大于 0 的数字。",
        default=DEFAULT_TOTAL_CAPACITY_QTY,
    )
    current_occupied = _parse_non_negative_int(
        form.get("current_occupied_qty"),
        "当前占用量必须是大于或等于 0 的数字。",
        default=0,
    )
    expected_inbound = _parse_non_negative_int(
        form.get("expected_inbound_qty"),
        "预计入库量必须是大于或等于 0 的数字。",
        default=0,
    )
    expected_outbound = _parse_non_negative_int(
        form.get("expected_outbound_qty"),
        "预计出库量必须是大于或等于 0 的数字。",
        default=0,
    )
    warning_threshold = _parse_non_negative_int(
        form.get("warning_threshold_qty"),
        "预警阈值必须是大于或等于 0 的数字。",
        default=DEFAULT_WARNING_THRESHOLD_QTY,
    )
    projected_value = str(form.get("projected_occupied_qty") or "").strip()
    if projected_value:
        projected_occupied = _parse_non_negative_int(
            projected_value,
            "预计占用量必须是大于或等于 0 的数字。",
            default=0,
        )
    else:
        projected_occupied = current_occupied + expected_inbound - expected_outbound
        if projected_occupied < 0:
            projected_occupied = 0
    remaining_value = str(form.get("remaining_capacity_qty") or "").strip()
    if remaining_value:
        remaining_capacity = _parse_int(
            remaining_value,
            "剩余容量必须是整数。",
            default=total_capacity - projected_occupied,
        )
    else:
        remaining_capacity = total_capacity - projected_occupied

    try:
        active = parse_bool(form.get("active", "true"), "active")
    except ValidationError as exc:
        raise ColdStorageInputError("请选择是否启用。") from exc

    note = str(form.get("note") or "").strip()
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
                raise ColdStorageInputError("同一业务日期已经存在启用的冷库状态，请先编辑或停用原记录。")

    return ColdStorageForm(
        trade_date=trade_date,
        total_capacity_qty=total_capacity,
        current_occupied_qty=current_occupied,
        expected_inbound_qty=expected_inbound,
        expected_outbound_qty=expected_outbound,
        warning_threshold_qty=warning_threshold,
        projected_occupied_qty=projected_occupied,
        remaining_capacity_qty=remaining_capacity,
        active=active,
        note=note,
    )


def apply_cold_storage_input(rows: list[dict[str, object]], form: ColdStorageForm) -> ColdStorageSaveResult:
    new_rows = [dict(row) for row in rows]
    new_rows.append(_form_to_row(form))
    return ColdStorageSaveResult(
        rows=new_rows,
        message="已新增冷库状态。保存后如需影响脚本状态和复核，请运行对应自动规则评估。",
    )


def apply_cold_storage_edit(
    rows: list[dict[str, object]],
    current_trade_date: str,
    form: ColdStorageForm,
    current_row_index: int | None = None,
) -> ColdStorageSaveResult:
    current_key = str(current_trade_date or "").strip()
    if current_row_index is None and not current_key:
        raise ColdStorageInputError("缺少要编辑的业务日期。")
    new_rows: list[dict[str, object]] = []
    updated = False
    for index, row in enumerate(rows):
        should_update = (
            index == current_row_index if current_row_index is not None else _date_key(row.get("trade_date")) == current_key
        )
        if should_update and not updated:
            new_rows.append(_form_to_row(form))
            updated = True
        else:
            new_rows.append(dict(row))
    if not updated:
        raise ColdStorageInputError("没有找到要编辑的冷库状态。")
    return ColdStorageSaveResult(
        rows=new_rows,
        message="已保存冷库状态。保存后如需影响脚本状态和复核，请运行对应自动规则评估。",
    )


def active_display(value: object) -> str:
    try:
        return "是" if parse_bool(value, "active") else "否"
    except ValidationError:
        return "否"


def format_cold_storage_number(value: object) -> str:
    if value in (None, ""):
        return "-"
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def computed_projected_occupied_from_row(row: dict[str, object]) -> int:
    projected = row.get("projected_occupied_qty")
    if projected not in (None, ""):
        return _parse_non_negative_int(projected, "预计占用量必须是大于或等于 0 的数字。", default=0)
    current = _parse_non_negative_int(row.get("current_occupied_qty"), "当前占用量必须是大于或等于 0 的数字。", default=0)
    inbound = _parse_non_negative_int(row.get("expected_inbound_qty"), "预计入库量必须是大于或等于 0 的数字。", default=0)
    outbound = _parse_non_negative_int(row.get("expected_outbound_qty"), "预计出库量必须是大于或等于 0 的数字。", default=0)
    return max(0, current + inbound - outbound)


def computed_remaining_capacity_from_row(row: dict[str, object]) -> int:
    remaining = row.get("remaining_capacity_qty")
    if remaining not in (None, ""):
        return _parse_int(remaining, "剩余容量必须是整数。", default=0)
    total = _parse_positive_int(row.get("total_capacity_qty"), "冷库总容量必须是大于 0 的数字。", default=DEFAULT_TOTAL_CAPACITY_QTY)
    return total - computed_projected_occupied_from_row(row)


def _form_to_row(form: ColdStorageForm) -> dict[str, object]:
    return {
        "trade_date": form.trade_date.isoformat(),
        "total_capacity_qty": form.total_capacity_qty,
        "current_occupied_qty": form.current_occupied_qty,
        "expected_inbound_qty": form.expected_inbound_qty,
        "expected_outbound_qty": form.expected_outbound_qty,
        "warning_threshold_qty": form.warning_threshold_qty,
        "projected_occupied_qty": form.projected_occupied_qty,
        "remaining_capacity_qty": form.remaining_capacity_qty,
        "active": form.active,
        "note": form.note,
    }


def _row_for_save(row: dict[str, object]) -> dict[str, object]:
    saved = dict(row)
    saved.setdefault("total_capacity_qty", DEFAULT_TOTAL_CAPACITY_QTY)
    saved.setdefault("current_occupied_qty", 0)
    saved.setdefault("expected_inbound_qty", 0)
    saved.setdefault("expected_outbound_qty", 0)
    saved.setdefault("warning_threshold_qty", DEFAULT_WARNING_THRESHOLD_QTY)
    if saved.get("projected_occupied_qty") in (None, ""):
        saved["projected_occupied_qty"] = computed_projected_occupied_from_row(saved)
    if saved.get("remaining_capacity_qty") in (None, ""):
        saved["remaining_capacity_qty"] = computed_remaining_capacity_from_row(saved)
    saved.setdefault("active", True)
    saved.setdefault("note", "")
    return saved


def _parse_int(value: object, message: str, *, default: int) -> int:
    if value in (None, ""):
        return default
    text = str(value).strip()
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ColdStorageInputError(message) from exc
    if number != number.to_integral_value():
        raise ColdStorageInputError(message)
    return int(number)


def _parse_non_negative_int(value: object, message: str, *, default: int) -> int:
    number = _parse_int(value, message, default=default)
    if number < 0:
        raise ColdStorageInputError(message)
    return number


def _parse_positive_int(value: object, message: str, *, default: int) -> int:
    number = _parse_int(value, message, default=default)
    if number <= 0:
        raise ColdStorageInputError(message)
    return number


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

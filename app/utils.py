from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.exceptions import ValidationError


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        raise ValidationError(f"{field_name} is required")
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise ValidationError(f"{field_name} must be a boolean-like value")


def parse_decimal(value: Any, field_name: str) -> Decimal:
    if value is None or value == "":
        raise ValidationError(f"{field_name} is required")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValidationError(f"{field_name} must be numeric") from exc


def parse_int(value: Any, field_name: str) -> int:
    if value is None or value == "":
        raise ValidationError(f"{field_name} is required")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must be an integer") from exc


def parse_date(value: Any, field_name: str) -> date:
    if value is None or value == "":
        raise ValidationError(f"{field_name} is required")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be YYYY-MM-DD") from exc


def parse_datetime(value: Any, field_name: str) -> datetime:
    if value is None or value == "":
        raise ValidationError(f"{field_name} is required")
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be ISO datetime") from exc


def serialize_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.01")), "f")

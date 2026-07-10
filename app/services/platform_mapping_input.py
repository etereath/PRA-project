from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.exceptions import TableValidationError, ValidationError
from app.repositories.workbook_repository import load_table_records, save_table_records
from app.services.product_inventory_input import PLATFORM_OPTIONS


class PlatformMappingInputError(ValidationError):
    """面向运营平台维护表单的业务校验错误。"""


@dataclass(slots=True)
class PlatformSaveResult:
    rows: list[dict[str, object]]
    message: str
    level: str = "success"


def default_platform_mapping_rows() -> list[dict[str, object]]:
    return [_platform_row(platform_name) for platform_name in PLATFORM_OPTIONS]


def ensure_platform_mappings_workbook(path: Path) -> None:
    if path.exists():
        return
    persist_platform_mapping_rows(path, default_platform_mapping_rows())


def load_platform_mapping_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return default_platform_mapping_rows()
    return [_ensure_platform_mapping_defaults(row) for row in load_table_records("platform_mappings", path)]


def persist_platform_mapping_rows(path: Path, rows: list[dict[str, object]]) -> None:
    try:
        save_table_records("platform_mappings", path, [_ensure_platform_mapping_defaults(row) for row in rows])
    except TableValidationError as exc:
        first_issue = exc.issues[0] if exc.issues else None
        if first_issue is None:
            raise PlatformMappingInputError("平台映射表校验失败，请检查输入内容。") from exc
        raise PlatformMappingInputError(
            f"平台映射表校验失败：第 {first_issue.row_number} 行 {first_issue.field_name} {first_issue.message}"
        ) from exc


def platform_options_from_rows(rows: list[dict[str, object]]) -> list[str]:
    options: list[str] = []
    seen: set[str] = set()
    for row in rows:
        status = str(row.get("mapping_status") or "").strip().lower()
        platform_name = normalize_platform_name(row.get("platform_name"))
        if not platform_name or status in {"disabled", "inactive", "停用"}:
            continue
        if platform_name not in seen:
            seen.add(platform_name)
            options.append(platform_name)
    for platform_name in PLATFORM_OPTIONS:
        if platform_name not in seen:
            seen.add(platform_name)
            options.append(platform_name)
    return options


def apply_platform_input(rows: list[dict[str, object]], values: dict[str, str]) -> PlatformSaveResult:
    platform_name = normalize_platform_name(values.get("platform_name"))
    if not platform_name:
        raise PlatformMappingInputError("请输入平台名称。")
    existing_names = {normalize_platform_name(row.get("platform_name")) for row in rows}
    if platform_name in existing_names:
        raise PlatformMappingInputError("该平台已存在，请勿重复新增。")
    rows = [_ensure_platform_mapping_defaults(row) for row in rows]
    rows.append(_platform_row(platform_name))
    return PlatformSaveResult(
        rows=rows,
        message=f"已新增平台：{platform_name}。价格规则中的平台选项会使用最新平台列表。",
    )


def normalize_platform_name(value: object) -> str:
    return str(value or "").strip()


def _platform_row(platform_name: str) -> dict[str, object]:
    return {
        "mapping_id": f"PLATFORM-{_platform_code(platform_name)}",
        "internal_sku": "",
        "platform_name": platform_name,
        "platform_product_id": "",
        "platform_product_name": "",
        "search_keyword": platform_name,
        "mapping_status": "active",
        "last_verified_at": "",
        "remark": "平台占位记录；初始库存不绑定平台。",
    }


def _platform_code(platform_name: str) -> str:
    if platform_name in PLATFORM_OPTIONS:
        return str(PLATFORM_OPTIONS.index(platform_name) + 1).zfill(2)
    code = re.sub(r"[^A-Za-z0-9]+", "-", platform_name).strip("-").upper()
    return code or "CUSTOM"


def _ensure_platform_mapping_defaults(row: dict[str, object]) -> dict[str, object]:
    return {
        "mapping_id": row.get("mapping_id", ""),
        "internal_sku": row.get("internal_sku", ""),
        "platform_name": row.get("platform_name", ""),
        "platform_product_id": row.get("platform_product_id", ""),
        "platform_product_name": row.get("platform_product_name", ""),
        "search_keyword": row.get("search_keyword", ""),
        "mapping_status": row.get("mapping_status", "active"),
        "last_verified_at": row.get("last_verified_at", ""),
        "remark": row.get("remark", ""),
    }

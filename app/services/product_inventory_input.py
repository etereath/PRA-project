from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.exceptions import TableValidationError, ValidationError
from app.inventory_models import InventoryWriteResult
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import (
    PRODUCT_HEADERS,
    load_products,
    load_table_records,
    save_table_records,
)
from app.services.authoritative_inventory import InventoryApplicationService


GRADE_OPTIONS = ["A", "B", "C", "D", "E", "0"]
UNIT_OPTIONS = ["扎", "半扎", "枝"]
STEM_LENGTH_OPTIONS = ["跟随等级", "40", "45", "50", "55", "60", "65", "70"]
FOLLOW_GRADE_VALUE = "跟随等级"
GRADE_STEM_LENGTH_MAP = {
    "A": "65",
    "B": "60",
    "C": "55",
    "D": "50",
    "E": "45",
    "0": "0",
}

# 平台不参与库存录入、库存匹配或 SKU 生成。这里仅保留未来页面说明可用的展示常量。
PLATFORM_OPTIONS = ["寻梦", "花伍", "珍情", "花易宝", "蚂蚁", "花宝宝"]

VARIETY_CODE_MAP = {
    "艾莎": "AISHA",
    "卡罗拉": "KALUOLA",
    "荔枝泡泡": "LIZHIPAOPAO",
    "卡布奇诺": "CAPPUCCINO",
    "高原红": "GAOYUANHONG",
    "蜜桃雪山": "MITAOSHAN",
    "粉雪山": "FENXUESHAN",
    "白雪山": "BAIXUESHAN",
    "红袖": "HONGXIU",
}

UNIT_CODE_MAP = {
    "扎": "Z",
    "半扎": "HZ",
    "枝": "D",
}


class ProductInventoryInputError(ValidationError):
    """面向运营录入页面的业务校验错误。"""


@dataclass(slots=True)
class ProductInventoryForm:
    product_name: str
    grade: str
    stem_length: str
    unit: str
    base_cost: Decimal
    quantity: int
    sale_enabled: bool
    variety_code: str = ""


@dataclass(slots=True)
class ProductEditForm:
    internal_sku: str
    product_name: str
    grade: str
    stem_length: str
    unit: str
    base_cost: Decimal
    current_stock: int
    sale_enabled: bool


@dataclass(slots=True)
class ProductInventorySaveResult:
    rows: list[dict[str, object]]
    message: str
    level: str = "success"
    updated_sku: str | None = None


@dataclass(frozen=True, slots=True)
class AuthoritativeNewProductResult:
    internal_sku: str
    initialization: InventoryWriteResult
    inbound: InventoryWriteResult


def load_product_input_rows(products_path: Path) -> list[dict[str, object]]:
    if not products_path.exists():
        return []
    return load_table_records("products", products_path)


def save_product_input_rows(products_path: Path, rows: list[dict[str, object]]) -> None:
    normalized_rows = [_ensure_product_row_defaults(row) for row in rows]
    save_table_records("products", products_path, normalized_rows)


def extract_variety_options(rows: list[dict[str, object]]) -> list[str]:
    varieties = {normalize_product_name(row.get("product_name", "")) for row in rows}
    return sorted(item for item in varieties if item)


def validate_inventory_form(values: dict[str, str]) -> ProductInventoryForm:
    product_name = normalize_product_name(values.get("product_name", ""))
    if not product_name:
        raise ProductInventoryInputError("请选择或输入品种。")

    grade = normalize_grade(values.get("grade", ""))
    if not grade:
        raise ProductInventoryInputError("请选择等级。等级 0 是允许的，但不能为空。")

    stem_length = resolve_stem_length_for_grade(grade, values.get("stem_length", ""))
    if not stem_length:
        raise ProductInventoryInputError("请选择枝长/规格。")

    unit = normalize_unit(values.get("unit", ""))
    if not unit:
        raise ProductInventoryInputError("请选择单位。")

    base_cost = _parse_non_negative_decimal(values.get("base_cost", ""), "基础成本")
    quantity = _parse_positive_int(values.get("quantity", ""), "本次入库数量")
    sale_enabled = _parse_yes_no(values.get("sale_enabled", "true"), "是否允许销售")
    variety_code = normalize_variety_code(values.get("variety_code", ""))

    return ProductInventoryForm(
        product_name=product_name,
        grade=grade,
        stem_length=stem_length,
        unit=unit,
        base_cost=base_cost,
        quantity=quantity,
        sale_enabled=sale_enabled,
        variety_code=variety_code,
    )


def validate_product_edit_form(values: dict[str, str]) -> ProductEditForm:
    internal_sku = str(values.get("internal_sku", "")).strip()
    if not internal_sku:
        raise ProductInventoryInputError("缺少内部 SKU，无法保存商品资料。")

    product_name = normalize_product_name(values.get("product_name", ""))
    if not product_name:
        raise ProductInventoryInputError("请选择或输入品种。")

    grade = normalize_grade(values.get("grade", ""))
    if not grade:
        raise ProductInventoryInputError("请选择等级。等级 0 是允许的，但不能为空。")

    stem_length = resolve_stem_length_for_grade(grade, values.get("stem_length", ""))
    if not stem_length:
        raise ProductInventoryInputError("请选择枝长/规格。")

    unit = normalize_unit(values.get("unit", ""))
    if not unit:
        raise ProductInventoryInputError("请选择单位。")

    base_cost = _parse_non_negative_decimal(values.get("base_cost", ""), "基础成本")
    current_stock = _parse_non_negative_int(values.get("current_stock", ""), "当前库存")
    sale_enabled = _parse_yes_no(values.get("sale_enabled", "true"), "是否允许销售")

    return ProductEditForm(
        internal_sku=internal_sku,
        product_name=product_name,
        grade=grade,
        stem_length=stem_length,
        unit=unit,
        base_cost=base_cost,
        current_stock=current_stock,
        sale_enabled=sale_enabled,
    )


def apply_inventory_input(
    rows: list[dict[str, object]],
    form: ProductInventoryForm,
    *,
    inventory_authoritative: bool = False,
) -> ProductInventorySaveResult:
    if inventory_authoritative:
        raise ProductInventoryInputError(
            "数据库库存已成为唯一权威，请使用业务管理中的人工库存调整。"
        )
    rows = [_ensure_product_row_defaults(row) for row in rows]
    matches = _find_same_type_indexes(rows, form.product_name, form.grade, form.stem_length, form.unit)

    if len(matches) > 1:
        raise ProductInventoryInputError("已存在多条同类型商品资料，请先检查商品主表。")

    if len(matches) == 1:
        index = matches[0]
        row = rows[index]
        old_stock = _safe_int(row.get("current_stock"), default=0)
        new_stock = old_stock + form.quantity
        row["current_stock"] = str(new_stock)
        row["base_cost"] = _format_decimal(form.base_cost)
        row["sale_enabled"] = "True" if form.sale_enabled else "False"
        row["product_name"] = form.product_name
        row["grade"] = form.grade
        row["stem_length"] = form.stem_length
        row["unit"] = form.unit
        sku = str(row.get("internal_sku") or "").strip()
        return ProductInventorySaveResult(
            rows=rows,
            updated_sku=sku,
            message=(
                f"同类型商品已存在，本次保存会补充库存，并将基础成本、是否允许销售更新为本次填写值。"
                f" 已补充库存：当前库存从 {old_stock} 增加到 {new_stock}。"
            ),
        )

    sku = generate_product_sku(
        form.product_name,
        form.grade,
        form.stem_length,
        form.unit,
        rows,
        variety_code=form.variety_code,
    )
    rows.append(
        {
            "internal_sku": sku,
            "product_name": form.product_name,
            "grade": form.grade,
            "stem_length": form.stem_length,
            "unit": form.unit,
            "base_cost": _format_decimal(form.base_cost),
            "current_stock": str(form.quantity),
            "sale_enabled": "True" if form.sale_enabled else "False",
            "last_price": "",
            "recommended_price": "",
            "remark": "通过业务输入录入库存",
            "feature_season": "",
            "feature_color": "",
        }
    )
    return ProductInventorySaveResult(
        rows=rows,
        updated_sku=sku,
        message="已新增商品资料并录入库存。保存的是业务输入数据。若要影响任务中心，请重新生成运行态任务。",
    )


def create_authoritative_product_with_inbound(
    products_path: Path,
    runtime_repository: SQLiteRuntimeRepository,
    form: ProductInventoryForm,
    *,
    actor: str,
    idempotency_key: str,
) -> AuthoritativeNewProductResult:
    """Persist metadata at workbook stock zero, then initialize and inbound in DB."""

    authority = InventoryRepository(runtime_repository).get_authority_state()
    if authority.authority_mode != "DB_AUTHORITY":
        raise ProductInventoryInputError("数据库库存尚未成为唯一权威。")
    normalized_actor = str(actor).strip()
    normalized_key = str(idempotency_key).strip()
    if not normalized_actor or not normalized_key:
        raise ProductInventoryInputError("新增商品缺少操作人或幂等键。")

    rows = [_ensure_product_row_defaults(row) for row in load_product_input_rows(products_path)]
    matches = _find_same_type_indexes(
        rows,
        form.product_name,
        form.grade,
        form.stem_length,
        form.unit,
    )
    if len(matches) > 1:
        raise ProductInventoryInputError("已存在多条同类型商品资料，请先检查商品主表。")
    if matches:
        row = rows[matches[0]]
        internal_sku = str(row.get("internal_sku") or "").strip()
        if (
            _safe_int(row.get("current_stock"), default=-1) != 0
            or str(row.get("base_cost") or "").strip()
            != _format_decimal(form.base_cost)
            or str(row.get("sale_enabled") or "").strip().lower()
            != str(bool(form.sale_enabled)).lower()
        ):
            raise ProductInventoryInputError(
                "同类型商品资料已存在但内容不同，不能作为新增商品请求重放。"
            )
    else:
        internal_sku = generate_product_sku(
            form.product_name,
            form.grade,
            form.stem_length,
            form.unit,
            rows,
            variety_code=form.variety_code,
        )
        rows.append(
            {
                "internal_sku": internal_sku,
                "product_name": form.product_name,
                "grade": form.grade,
                "stem_length": form.stem_length,
                "unit": form.unit,
                "base_cost": _format_decimal(form.base_cost),
                "current_stock": "0",
                "sale_enabled": "True" if form.sale_enabled else "False",
                "last_price": "",
                "recommended_price": "",
                "remark": "数据库权威模式新增商品资料",
                "feature_season": "",
                "feature_color": "",
            }
        )
        persist_product_rows(products_path, rows)

    def _product_exists(sku: str) -> bool:
        return any(item.internal_sku == sku for item in load_products(products_path))

    inventory = InventoryApplicationService(
        runtime_repository,
        product_exists=_product_exists,
    )
    initialization = inventory.initialize_sku(
        internal_sku=internal_sku,
        actor=normalized_actor,
        idempotency_key=f"{normalized_key}:sku-initialization",
    )
    inbound = inventory.adjust(
        internal_sku=internal_sku,
        inventory_delta=form.quantity,
        source_type="NEW_FLOWER_INBOUND",
        reason="新花入库",
        actor=normalized_actor,
        idempotency_key=f"{normalized_key}:inbound",
        expected_version=1,
    )
    return AuthoritativeNewProductResult(
        internal_sku=internal_sku,
        initialization=initialization,
        inbound=inbound,
    )


def apply_product_edit(
    rows: list[dict[str, object]],
    form: ProductEditForm,
    *,
    inventory_authoritative: bool = False,
) -> ProductInventorySaveResult:
    rows = [_ensure_product_row_defaults(row) for row in rows]
    target_index = next(
        (index for index, row in enumerate(rows) if str(row.get("internal_sku") or "").strip() == form.internal_sku),
        None,
    )
    if target_index is None:
        raise ProductInventoryInputError("未找到要编辑的商品资料。")

    duplicate_indexes = _find_same_type_indexes(
        rows,
        form.product_name,
        form.grade,
        form.stem_length,
        form.unit,
        exclude_sku=form.internal_sku,
    )
    if duplicate_indexes:
        raise ProductInventoryInputError("修改后会与已有商品资料重复，请检查品种、等级、规格和单位。")

    row = rows[target_index]
    stored_stock = _safe_int(row.get("current_stock"), default=0)
    if inventory_authoritative and form.current_stock != stored_stock:
        raise ProductInventoryInputError(
            "数据库库存已成为唯一权威，商品资料编辑不能修改工作簿库存。"
        )
    row["product_name"] = form.product_name
    row["grade"] = form.grade
    row["stem_length"] = form.stem_length
    row["unit"] = form.unit
    row["base_cost"] = _format_decimal(form.base_cost)
    if not inventory_authoritative:
        row["current_stock"] = str(form.current_stock)
    row["sale_enabled"] = "True" if form.sale_enabled else "False"
    return ProductInventorySaveResult(
        rows=rows,
        updated_sku=form.internal_sku,
        message="商品资料已保存。SKU 已保留不变；若要影响任务中心，请重新生成运行态任务。",
    )


def normalize_product_type(product_name: object, grade: object, stem_length: object, unit: object) -> tuple[str, str, str, str]:
    normalized_grade = normalize_grade(grade)
    return (
        normalize_product_name(product_name),
        normalized_grade,
        resolve_stem_length_for_grade(normalized_grade, stem_length),
        normalize_unit(unit),
    )


def normalize_product_name(value: object) -> str:
    return str(value or "").strip()


def normalize_grade(value: object) -> str:
    normalized = str(value if value is not None else "").strip().upper()
    if normalized in GRADE_OPTIONS:
        return normalized
    return ""


def normalize_stem_length(value: object) -> str:
    normalized = str(value or "").strip()
    if normalized.lower() in {"follow_grade", "fg"} or normalized == "跟随等级":
        return FOLLOW_GRADE_VALUE
    if normalized in STEM_LENGTH_OPTIONS or normalized == "0":
        return normalized
    return ""


def resolve_stem_length_for_grade(grade: object, stem_length: object) -> str:
    normalized_grade = normalize_grade(grade)
    normalized_stem = normalize_stem_length(stem_length)
    if normalized_stem == FOLLOW_GRADE_VALUE:
        return GRADE_STEM_LENGTH_MAP.get(normalized_grade, "")
    return normalized_stem


def normalize_unit(value: object) -> str:
    normalized = str(value or "").strip()
    aliases = {
        "bundle": "扎",
        "bunch": "扎",
        "half_bundle": "半扎",
        "stem": "枝",
    }
    normalized = aliases.get(normalized.lower(), normalized)
    if normalized in UNIT_OPTIONS:
        return normalized
    return ""


def normalize_variety_code(value: object) -> str:
    raw = str(value or "").strip().upper()
    return re.sub(r"[^A-Z0-9]+", "", raw)


def generate_product_sku(
    product_name: str,
    grade: str,
    stem_length: str,
    unit: str,
    existing_rows: list[dict[str, object]],
    *,
    variety_code: str = "",
) -> str:
    code = normalize_variety_code(variety_code) or VARIETY_CODE_MAP.get(product_name, "")
    if not code:
        raise ProductInventoryInputError("该品种尚未维护品种代码，请先填写品种代码后再录入库存。")

    stem_code = resolve_stem_length_for_grade(grade, stem_length)
    unit_code = UNIT_CODE_MAP[normalize_unit(unit)]
    base_sku = f"{code}-{normalize_grade(grade)}-{stem_code}-{unit_code}"
    used_skus = {str(row.get("internal_sku") or "").strip() for row in existing_rows}
    if base_sku not in used_skus:
        return base_sku

    for suffix in range(2, 1000):
        candidate = f"{base_sku}-{suffix}"
        if candidate not in used_skus:
            return candidate
    raise ProductInventoryInputError("SKU 冲突过多，请先检查商品主表。")


def sale_enabled_display(value: object) -> str:
    try:
        return "是" if _parse_yes_no(value, "是否允许销售") else "否"
    except ProductInventoryInputError:
        return str(value or "-")


def format_product_number(value: object) -> str:
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


def _find_same_type_indexes(
    rows: list[dict[str, object]],
    product_name: str,
    grade: str,
    stem_length: str,
    unit: str,
    *,
    exclude_sku: str = "",
) -> list[int]:
    target = normalize_product_type(product_name, grade, stem_length, unit)
    matches: list[int] = []
    for index, row in enumerate(rows):
        sku = str(row.get("internal_sku") or "").strip()
        if exclude_sku and sku == exclude_sku:
            continue
        current = normalize_product_type(
            row.get("product_name", ""),
            row.get("grade", ""),
            row.get("stem_length", ""),
            row.get("unit", ""),
        )
        if current == target:
            matches.append(index)
    return matches


def _ensure_product_row_defaults(row: dict[str, object]) -> dict[str, object]:
    return {header: row.get(header, "") for header in PRODUCT_HEADERS}


def _parse_non_negative_decimal(value: object, field_label: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise ProductInventoryInputError(f"{field_label}必须是数字。") from None
    if decimal_value < 0:
        raise ProductInventoryInputError(f"{field_label}必须大于或等于 0。")
    return decimal_value


def _parse_positive_int(value: object, field_label: str) -> int:
    parsed = _parse_non_negative_int(value, field_label)
    if parsed <= 0:
        raise ProductInventoryInputError(f"{field_label}必须大于 0。")
    return parsed


def _parse_non_negative_int(value: object, field_label: str) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise ProductInventoryInputError(f"{field_label}必须是整数。") from None
    if parsed < 0:
        raise ProductInventoryInputError(f"{field_label}不得小于 0。")
    return parsed


def _parse_yes_no(value: object, field_label: str) -> bool:
    normalized = str(value if value is not None else "").strip().lower()
    if normalized in {"true", "1", "yes", "y", "是"}:
        return True
    if normalized in {"false", "0", "no", "n", "否"}:
        return False
    raise ProductInventoryInputError(f"{field_label}必须选择是或否。")


def _safe_int(value: object, *, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _format_decimal(value: Decimal) -> str:
    if value == value.to_integral():
        return str(int(value))
    return format(value.normalize(), "f")


def persist_product_rows(products_path: Path, rows: list[dict[str, object]]) -> None:
    try:
        save_product_input_rows(products_path, rows)
    except TableValidationError as exc:
        first_issue = exc.issues[0] if exc.issues else None
        if first_issue is None:
            raise ProductInventoryInputError("商品主表校验失败，请检查输入内容。") from exc
        raise ProductInventoryInputError(
            f"商品主表校验失败：第 {first_issue.row_number} 行 {first_issue.field_name} {first_issue.message}"
        ) from exc

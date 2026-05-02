from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
import tempfile
from typing import Iterable
from uuid import uuid4

from openpyxl import Workbook, load_workbook

from app.enums import ConditionType, ListingAction, PricingMethod, PricingSource, RoundingRule, TaskActionType, TaskStatus
from app.exceptions import TableValidationError, TableValidationIssue, ValidationError
from app.models import ExecutionLog, ListingRule, PriceRule, Product, Task
from app.utils import parse_bool, parse_decimal, parse_int, serialize_decimal


PRODUCT_HEADERS = [
    "internal_sku",
    "product_name",
    "variety",
    "grade",
    "stem_length",
    "unit",
    "base_cost",
    "current_stock",
    "sale_enabled",
    "last_price",
    "remark",
    "feature_season",
    "feature_color",
]

PRICE_RULE_HEADERS = [
    "rule_id",
    "rule_name",
    "scope_type",
    "scope_value",
    "pricing_method",
    "markup_value",
    "min_price",
    "rounding_rule",
    "rounding_step",
    "active",
    "priority",
    "remark",
]

LISTING_RULE_HEADERS = [
    "rule_id",
    "rule_name",
    "condition_type",
    "condition_value",
    "action",
    "active",
    "priority",
    "remark",
]

TASK_HEADERS = [
    "task_id",
    "internal_sku",
    "platform_name",
    "action_type",
    "priority",
    "task_status",
    "created_at",
    "target_price",
    "target_status",
    "pricing_source",
    "decision_trace",
    "result_message",
]

EXECUTION_LOG_HEADERS = [
    "log_id",
    "task_id",
    "executor_name",
    "start_time",
    "end_time",
    "success_flag",
    "error_code",
    "error_message",
    "raw_output",
    "ai_model_version",
    "ai_summary",
]

TABLE_HEADERS = {
    "products": PRODUCT_HEADERS,
    "price_rules": PRICE_RULE_HEADERS,
    "listing_rules": LISTING_RULE_HEADERS,
}


def create_template_workbooks(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        _create_template(output_dir / "products_template.xlsx", PRODUCT_HEADERS),
        _create_template(output_dir / "price_rules_template.xlsx", PRICE_RULE_HEADERS),
        _create_template(output_dir / "listing_rules_template.xlsx", LISTING_RULE_HEADERS),
    ]
    return paths


def _create_template(path: Path, headers: list[str]) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"
    sheet.append(headers)
    workbook.save(path)
    return path


def load_products(path: Path) -> list[Product]:
    rows = _read_rows(path, PRODUCT_HEADERS)
    products: list[Product] = []
    seen_skus: set[str] = set()
    for index, row in enumerate(rows, start=2):
        sku = _required_text(row["internal_sku"], "internal_sku", index)
        if sku in seen_skus:
            raise ValidationError(f"products row {index}: duplicate internal_sku '{sku}'")
        seen_skus.add(sku)
        metadata = {
            "feature_season": row.get("feature_season") or "",
            "feature_color": row.get("feature_color") or "",
        }
        products.append(
            Product(
                internal_sku=sku,
                product_name=_required_text(row["product_name"], "product_name", index),
                variety=_required_text(row["variety"], "variety", index),
                grade=_required_text(row["grade"], "grade", index),
                stem_length=_required_text(row["stem_length"], "stem_length", index),
                unit=_required_text(row["unit"], "unit", index),
                base_cost=parse_decimal(row["base_cost"], f"products row {index} base_cost"),
                current_stock=parse_int(row["current_stock"], f"products row {index} current_stock"),
                sale_enabled=parse_bool(row["sale_enabled"], f"products row {index} sale_enabled"),
                last_price=parse_decimal(row["last_price"], f"products row {index} last_price")
                if row.get("last_price") not in ("", None)
                else None,
                remark=str(row.get("remark") or ""),
                metadata=metadata,
            )
        )
    return products


def load_price_rules(path: Path) -> list[PriceRule]:
    rows = _read_rows(path, PRICE_RULE_HEADERS)
    rules: list[PriceRule] = []
    for index, row in enumerate(rows, start=2):
        rules.append(
            PriceRule(
                rule_id=_required_text(row["rule_id"], "rule_id", index),
                rule_name=_required_text(row["rule_name"], "rule_name", index),
                scope_type=_required_text(row["scope_type"], "scope_type", index),
                scope_value=_required_text(row["scope_value"], "scope_value", index),
                pricing_method=PricingMethod(_required_text(row["pricing_method"], "pricing_method", index)),
                markup_value=parse_decimal(row["markup_value"], f"price_rules row {index} markup_value"),
                min_price=parse_decimal(row["min_price"], f"price_rules row {index} min_price")
                if row.get("min_price") not in ("", None)
                else None,
                rounding_rule=RoundingRule(_required_text(row["rounding_rule"], "rounding_rule", index)),
                rounding_step=parse_decimal(row["rounding_step"], f"price_rules row {index} rounding_step")
                if row.get("rounding_step") not in ("", None)
                else None,
                active=parse_bool(row["active"], f"price_rules row {index} active"),
                priority=parse_int(row["priority"], f"price_rules row {index} priority"),
                remark=str(row.get("remark") or ""),
            )
        )
    return rules


def load_listing_rules(path: Path) -> list[ListingRule]:
    rows = _read_rows(path, LISTING_RULE_HEADERS)
    rules: list[ListingRule] = []
    for index, row in enumerate(rows, start=2):
        rules.append(
            ListingRule(
                rule_id=_required_text(row["rule_id"], "rule_id", index),
                rule_name=_required_text(row["rule_name"], "rule_name", index),
                condition_type=ConditionType(_required_text(row["condition_type"], "condition_type", index)),
                condition_value=parse_decimal(
                    row["condition_value"], f"listing_rules row {index} condition_value"
                )
                if row.get("condition_value") not in ("", None)
                else None,
                action=ListingAction(_required_text(row["action"], "action", index)),
                active=parse_bool(row["active"], f"listing_rules row {index} active"),
                priority=parse_int(row["priority"], f"listing_rules row {index} priority"),
                remark=str(row.get("remark") or ""),
            )
        )
    return rules


def export_tasks(path: Path, tasks: Iterable[Task]) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "tasks"
    sheet.append(TASK_HEADERS)
    for task in tasks:
        record = task.to_record()
        sheet.append(
            [
                record["task_id"],
                record["internal_sku"],
                record["platform_name"],
                record["action_type"],
                record["priority"],
                record["task_status"],
                record["created_at"],
                serialize_decimal(task.target_price),
                record["target_status"],
                record["pricing_source"],
                str(record["decision_trace"]),
                record["result_message"],
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def load_tasks(path: Path) -> list[Task]:
    rows = _read_rows(path, TASK_HEADERS)
    tasks: list[Task] = []
    for index, row in enumerate(rows, start=2):
        decision_trace: dict[str, object] = {}
        raw_trace = row.get("decision_trace")
        if raw_trace not in ("", None):
            parsed_trace = ast.literal_eval(str(raw_trace))
            if isinstance(parsed_trace, dict):
                decision_trace = parsed_trace
        tasks.append(
            Task(
                task_id=_required_text(row["task_id"], "task_id", index),
                internal_sku=_required_text(row["internal_sku"], "internal_sku", index),
                platform_name=_required_text(row["platform_name"], "platform_name", index),
                action_type=TaskActionType(_required_text(row["action_type"], "action_type", index)),
                priority=parse_int(row["priority"], f"tasks row {index} priority"),
                task_status=TaskStatus(_required_text(row["task_status"], "task_status", index)),
                created_at=datetime.fromisoformat(_required_text(row["created_at"], "created_at", index)),
                target_price=parse_decimal(row["target_price"], f"tasks row {index} target_price")
                if row.get("target_price") not in ("", None)
                else None,
                target_status=str(row["target_status"]) if row.get("target_status") not in ("", None) else None,
                pricing_source=PricingSource(str(row["pricing_source"]))
                if row.get("pricing_source") not in ("", None)
                else None,
                decision_trace=decision_trace,
                result_message=str(row.get("result_message") or ""),
            )
        )
    return tasks


def export_execution_logs(path: Path, logs: Iterable[ExecutionLog]) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "execution_logs"
    sheet.append(EXECUTION_LOG_HEADERS)
    for log in logs:
        sheet.append(
            [
                log.log_id or uuid4().hex[:12],
                log.task_id,
                log.executor_name,
                log.start_time.isoformat(),
                log.end_time.isoformat() if log.end_time is not None else None,
                log.success_flag,
                log.error_code,
                log.error_message,
                log.raw_output,
                log.ai_model_version,
                log.ai_summary,
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def load_table_records(table_name: str, path: Path) -> list[dict[str, object]]:
    headers = get_table_headers(table_name)
    return _read_rows(path, headers)


def save_table_records(table_name: str, path: Path, rows: list[dict[str, object]]) -> Path:
    headers = get_table_headers(table_name)
    _validate_table_records(table_name, rows)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def get_table_headers(table_name: str) -> list[str]:
    try:
        return TABLE_HEADERS[table_name]
    except KeyError as exc:
        raise ValidationError(f"unsupported table '{table_name}'") from exc


def _validate_table_records(table_name: str, rows: list[dict[str, object]]) -> None:
    issues = _collect_table_validation_issues(table_name, rows)
    if issues:
        raise TableValidationError(table_name, issues)

    headers = get_table_headers(table_name)
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "data"
        sheet.append(headers)
        for row in rows:
            sheet.append([row.get(header, "") for header in headers])
        workbook.save(temp_path)
        if table_name == "products":
            load_products(temp_path)
        elif table_name == "price_rules":
            load_price_rules(temp_path)
        elif table_name == "listing_rules":
            load_listing_rules(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _collect_table_validation_issues(table_name: str, rows: list[dict[str, object]]) -> list[TableValidationIssue]:
    if table_name == "products":
        return _collect_product_issues(rows)
    if table_name == "price_rules":
        return _collect_price_rule_issues(rows)
    if table_name == "listing_rules":
        return _collect_listing_rule_issues(rows)
    raise ValidationError(f"unsupported table '{table_name}'")


def _collect_product_issues(rows: list[dict[str, object]]) -> list[TableValidationIssue]:
    issues: list[TableValidationIssue] = []
    seen_skus: dict[str, int] = {}
    required_fields = ["internal_sku", "product_name", "variety", "grade", "stem_length", "unit"]

    for index, row in enumerate(rows, start=2):
        for field_name in required_fields:
            value = row.get(field_name)
            if value is None or str(value).strip() == "":
                issues.append(TableValidationIssue(index, field_name, "该字段必填"))

        sku = str(row.get("internal_sku") or "").strip()
        if sku:
            if sku in seen_skus:
                issues.append(TableValidationIssue(index, "internal_sku", f"与第 {seen_skus[sku]} 行重复"))
            else:
                seen_skus[sku] = index

        _try_parse_decimal(row.get("base_cost"), index, "base_cost", issues, required=True)
        _try_parse_int(row.get("current_stock"), index, "current_stock", issues, required=True)
        _try_parse_bool(row.get("sale_enabled"), index, "sale_enabled", issues, required=True)
        _try_parse_decimal(row.get("last_price"), index, "last_price", issues, required=False)

    return issues


def _collect_price_rule_issues(rows: list[dict[str, object]]) -> list[TableValidationIssue]:
    issues: list[TableValidationIssue] = []
    required_fields = ["rule_id", "rule_name", "scope_type", "scope_value", "pricing_method", "rounding_rule"]

    for index, row in enumerate(rows, start=2):
        for field_name in required_fields:
            value = row.get(field_name)
            if value is None or str(value).strip() == "":
                issues.append(TableValidationIssue(index, field_name, "该字段必填"))

        _try_enum(row.get("pricing_method"), PricingMethod, index, "pricing_method", issues)
        _try_decimal(row.get("markup_value"), index, "markup_value", issues, required=True)
        _try_decimal(row.get("min_price"), index, "min_price", issues, required=False)
        _try_enum(row.get("rounding_rule"), RoundingRule, index, "rounding_rule", issues)
        _try_decimal(row.get("rounding_step"), index, "rounding_step", issues, required=False)
        _try_parse_bool(row.get("active"), index, "active", issues, required=True)
        _try_parse_int(row.get("priority"), index, "priority", issues, required=True)

    return issues


def _collect_listing_rule_issues(rows: list[dict[str, object]]) -> list[TableValidationIssue]:
    issues: list[TableValidationIssue] = []
    required_fields = ["rule_id", "rule_name", "condition_type", "action"]

    for index, row in enumerate(rows, start=2):
        for field_name in required_fields:
            value = row.get(field_name)
            if value is None or str(value).strip() == "":
                issues.append(TableValidationIssue(index, field_name, "该字段必填"))

        _try_enum(row.get("condition_type"), ConditionType, index, "condition_type", issues)
        _try_decimal(row.get("condition_value"), index, "condition_value", issues, required=False)
        _try_enum(row.get("action"), ListingAction, index, "action", issues)
        _try_parse_bool(row.get("active"), index, "active", issues, required=True)
        _try_parse_int(row.get("priority"), index, "priority", issues, required=True)

    return issues


def _try_enum(value: object, enum_cls, row_number: int, field_name: str, issues: list[TableValidationIssue]) -> None:
    if value in (None, ""):
        return
    try:
        enum_cls(str(value).strip())
    except ValueError:
        allowed = ", ".join(item.value for item in enum_cls)
        issues.append(TableValidationIssue(row_number, field_name, f"值无效，可选：{allowed}"))


def _try_decimal(
    value: object,
    row_number: int,
    field_name: str,
    issues: list[TableValidationIssue],
    *,
    required: bool,
) -> None:
    _try_parse_decimal(value, row_number, field_name, issues, required=required)


def _try_parse_decimal(
    value: object,
    row_number: int,
    field_name: str,
    issues: list[TableValidationIssue],
    *,
    required: bool,
) -> None:
    if value in (None, ""):
        if required:
            issues.append(TableValidationIssue(row_number, field_name, "该字段必填"))
        return
    try:
        parse_decimal(value, f"row {row_number} {field_name}")
    except ValidationError:
        issues.append(TableValidationIssue(row_number, field_name, "请输入数字"))


def _try_parse_int(
    value: object,
    row_number: int,
    field_name: str,
    issues: list[TableValidationIssue],
    *,
    required: bool,
) -> None:
    if value in (None, ""):
        if required:
            issues.append(TableValidationIssue(row_number, field_name, "该字段必填"))
        return
    try:
        parse_int(value, f"row {row_number} {field_name}")
    except ValidationError:
        issues.append(TableValidationIssue(row_number, field_name, "请输入整数"))


def _try_parse_bool(
    value: object,
    row_number: int,
    field_name: str,
    issues: list[TableValidationIssue],
    *,
    required: bool,
) -> None:
    if value in (None, ""):
        if required:
            issues.append(TableValidationIssue(row_number, field_name, "该字段必填"))
        return
    try:
        parse_bool(value, f"row {row_number} {field_name}")
    except ValidationError:
        issues.append(TableValidationIssue(row_number, field_name, "请输入 true/false、1/0、yes/no"))


def _read_rows(path: Path, expected_headers: list[str]) -> list[dict[str, object]]:
    workbook = load_workbook(path)
    sheet = workbook.active
    header_row = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    if header_row != expected_headers:
        raise ValidationError(
            f"{path.name}: invalid headers. Expected {expected_headers}, got {header_row}"
        )
    rows: list[dict[str, object]] = []
    for raw_row in sheet.iter_rows(min_row=2, values_only=True):
        if all(value in (None, "") for value in raw_row):
            continue
        rows.append(dict(zip(expected_headers, raw_row, strict=True)))
    return rows


def _required_text(value: object, field_name: str, row_number: int) -> str:
    if value is None or str(value).strip() == "":
        raise ValidationError(f"row {row_number}: {field_name} is required")
    return str(value).strip()

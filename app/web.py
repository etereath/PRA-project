from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from html import escape
from http.cookies import SimpleCookie
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import make_server

from app.enums import NotificationSendStatus, ReviewTaskStatus, TaskStatus
from app.exceptions import TableValidationError, ValidationError
from app.field_labels import FIELD_LABELS, TABLE_LABELS
from app.repositories.workbook_repository import (
    get_table_headers,
    load_table_records,
    save_table_records,
)
from app.services.workflow import (
    ExecutionSimulationInputs,
    ExecutionSimulationSummary,
    RuntimeReviewResolutionInputs,
    TaskGenerationSummary,
    ValidationSummary,
    WorkflowInputs,
    generate_tasks_from_sources,
    get_runtime_notification_log,
    get_runtime_review_task,
    get_runtime_task,
    list_runtime_notification_logs,
    list_runtime_task_history,
    list_runtime_review_tasks,
    list_runtime_tasks,
    list_manual_intervention_tasks,
    preview_tasks_from_sources,
    resolve_runtime_review_task,
    simulate_execution_from_tasks,
    validate_sources,
)
from app.services.runtime import DEFAULT_RUNTIME_DB
from app.utils import parse_date


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRODUCTS = ROOT / "data" / "samples" / "products.xlsx"
DEFAULT_PRICE_RULES = ROOT / "data" / "samples" / "price_rules.xlsx"
DEFAULT_LISTING_RULES = ROOT / "data" / "samples" / "listing_rules.xlsx"
DEFAULT_OUTPUT = ROOT / "data" / "samples" / "web_generated_tasks.xlsx"
DEFAULT_EXECUTION_LOGS = ROOT / "data" / "samples" / "web_execution_logs.xlsx"
DEFAULT_EXECUTED_TASKS = ROOT / "data" / "samples" / "web_executed_tasks.xlsx"
DEFAULT_HARVEST_FORECASTS = ROOT / "data" / "samples" / "harvest_forecasts.xlsx"
DEFAULT_PRICE_FORECASTS = ROOT / "data" / "samples" / "price_forecasts.xlsx"
DEFAULT_CAPACITY_PLANS = ROOT / "data" / "samples" / "capacity_plans.xlsx"
DEFAULT_COLD_STORAGE_STATUS = ROOT / "data" / "samples" / "cold_storage_status.xlsx"
DEFAULT_MANUAL_TASKS = ROOT / "data" / "samples" / "web_generated_tasks.xlsx"

TABLE_OPTIONS = {
    "products": {"label": TABLE_LABELS["products"], "path": DEFAULT_PRODUCTS},
    "price_rules": {"label": TABLE_LABELS["price_rules"], "path": DEFAULT_PRICE_RULES},
    "listing_rules": {"label": TABLE_LABELS["listing_rules"], "path": DEFAULT_LISTING_RULES},
    "harvest_forecasts": {"label": TABLE_LABELS["harvest_forecasts"], "path": DEFAULT_HARVEST_FORECASTS},
    "price_forecasts": {"label": TABLE_LABELS["price_forecasts"], "path": DEFAULT_PRICE_FORECASTS},
    "capacity_plans": {"label": TABLE_LABELS["capacity_plans"], "path": DEFAULT_CAPACITY_PLANS},
    "cold_storage_status": {"label": TABLE_LABELS["cold_storage_status"], "path": DEFAULT_COLD_STORAGE_STATUS},
}

TABLE_HEADER_LABELS = FIELD_LABELS
RUNTIME_SESSION_COOKIE = "pra_runtime_session"
RUNTIME_SESSION_TTL_SECONDS = 3600
_RUNTIME_SESSIONS: dict[str, dict[str, object]] = {}

UI_TEXT = {
    "site_title": "PRA \u7ba1\u7406\u53f0",
    "dashboard_title": "PRA \u7ba1\u7406\u53f0",
    "dashboard_lede": "\u5f53\u524d\u9875\u9762\u7528\u4e8e\u6821\u9a8c Excel \u6570\u636e\u3001\u751f\u6210\u4efb\u52a1\uff0c\u5e76\u5728\u4fdd\u7559 AI \u5b9a\u4ef7\u5efa\u8bae\u5f00\u5173\u7684\u524d\u63d0\u4e0b\u5feb\u901f\u9a8c\u8bc1\u6574\u6761\u4e1a\u52a1\u94fe\u8def\u3002",
    "dashboard_tab": "\u4efb\u52a1\u9762\u677f",
    "tables_tab": "Excel \u8868\u683c\u7ba1\u7406",
    "execution_tab": "\u6267\u884c\u56de\u5199",
    "manual_tab": "\u4eba\u5de5\u4ecb\u5165",
    "runtime_tab": "SQLite \u8fd0\u884c\u6001",
    "task_panel_title": "\u4efb\u52a1\u751f\u6210",
    "execution_panel_title": "\u6a21\u62df\u6267\u884c\u4e0e\u56de\u5199",
    "resources_title": "\u5185\u7f6e\u8d44\u6e90",
    "products_path": "\u5546\u54c1\u8868\u8def\u5f84",
    "price_rules_path": "\u4ef7\u683c\u89c4\u5219\u8def\u5f84",
    "listing_rules_path": "\u4e0a\u4e0b\u67b6\u89c4\u5219\u8def\u5f84",
    "output_path": "\u4efb\u52a1\u8f93\u51fa\u8def\u5f84",
    "platform_name": "\u5e73\u53f0\u540d\u79f0",
    "inventory_strategy": "\u5e93\u5b58\u7b56\u7565",
    "inventory_strategy_conservative": "\u4fdd\u5b88\u7b56\u7565\uff08\u4f18\u5148\u63a7\u5236\u8d85\u552e\u98ce\u9669\uff09",
    "inventory_strategy_balanced": "\u5e73\u8861\u7b56\u7565\uff08\u5728\u98ce\u9669\u53ef\u63a7\u524d\u63d0\u4e0b\u63d0\u9ad8\u53ef\u552e\u91cf\uff09",
    "use_mock_ai": "\u4f7f\u7528 Mock AI \u5b9a\u4ef7\u5efa\u8bae",
    "validate_button": "\u5148\u6821\u9a8c\u6570\u636e",
    "preview_button": "\u9884\u89c8\u4efb\u52a1",
    "confirm_button": "\u786e\u8ba4\u5bfc\u51fa\u4efb\u52a1",
    "data_summary": "\u6570\u636e\u6458\u8981",
    "task_result": "\u4efb\u52a1\u7ed3\u679c",
    "output_file": "\u8f93\u51fa\u6587\u4ef6",
    "planned_output_file": "\u9884\u8ba1\u5bfc\u51fa\u6587\u4ef6",
    "no_tasks": "\u6682\u65e0\u4efb\u52a1",
    "preview_ready": "\u4efb\u52a1\u9884\u89c8\u5df2\u5b8c\u6210\uff0c\u786e\u8ba4\u65e0\u8bef\u540e\u518d\u5199\u5165 Excel \u6587\u4ef6\u3002",
    "execution_source_path": "\u4efb\u52a1\u6587\u4ef6\u8def\u5f84",
    "execution_logs_path": "\u6267\u884c\u65e5\u5fd7\u8f93\u51fa\u8def\u5f84",
    "execution_tasks_path": "\u66f4\u65b0\u540e\u4efb\u52a1\u8f93\u51fa\u8def\u5f84",
    "executor_name": "\u6267\u884c\u5668\u540d\u79f0",
    "simulate_button": "\u6a21\u62df\u6267\u884c\u5e76\u56de\u5199",
    "execution_result": "\u6267\u884c\u56de\u5199\u7ed3\u679c",
    "execution_logs_file": "\u6267\u884c\u65e5\u5fd7\u6587\u4ef6",
    "execution_updated_tasks_file": "\u66f4\u65b0\u540e\u4efb\u52a1\u6587\u4ef6",
    "table_editor_title": "Excel \u8868\u683c\u7ba1\u7406",
    "table_editor_lede": "\u5728\u8fd9\u4e00\u9875\u91cc\u6211\u4eec\u53ef\u4ee5\u76f4\u63a5\u7ef4\u62a4\u5546\u54c1\u4e3b\u8868\u3001\u4ef7\u683c\u89c4\u5219\u8868\u548c\u4e0a\u4e0b\u67b6\u89c4\u5219\u8868\u3002\u5148\u52a0\u8f7d\uff0c\u518d\u7f16\u8f91\uff0c\u6700\u540e\u4fdd\u5b58\u56de\u5bf9\u5e94\u5de5\u4f5c\u7c3f\u3002",
    "table_picker": "\u8868\u683c\u9009\u62e9",
    "table_type": "\u8868\u683c\u7c7b\u578b",
    "table_path": "\u5de5\u4f5c\u7c3f\u8def\u5f84",
    "load_button": "\u52a0\u8f7d\u8868\u683c",
    "save_button": "\u4fdd\u5b58\u5f53\u524d\u4fee\u6539",
    "table_hint": "\u8868\u683c\u4f1a\u989d\u5916\u4fdd\u7559 3 \u884c\u7a7a\u767d\u8f93\u5165\uff0c\u65b9\u4fbf\u76f4\u63a5\u8ffd\u52a0\u65b0\u8bb0\u5f55\u3002\u4fdd\u5b58\u65f6\u4f1a\u81ea\u52a8\u5ffd\u7565\u6574\u884c\u7a7a\u767d\u3002",
    "loaded_rows": "\u5df2\u52a0\u8f7d {count} \u884c\u6570\u636e\u3002",
    "saved_rows": "\u5df2\u4fdd\u5b58 {count} \u884c\u5230 {path}",
    "validated": "\u6570\u636e\u6821\u9a8c\u901a\u8fc7\uff0c\u53ef\u4ee5\u76f4\u63a5\u751f\u6210\u4efb\u52a1\u3002",
    "generated": "\u5df2\u751f\u6210 {count} \u6761\u4efb\u52a1\uff0c\u5e76\u5199\u5165 {path}",
    "previewed": "\u5df2\u9884\u89c8 {count} \u6761\u4efb\u52a1\uff0c\u5c1a\u672a\u5199\u5165 Excel \u3002",
    "execution_done": "\u5df2\u6a21\u62df\u6267\u884c {count} \u6761\u4efb\u52a1\uff0c\u65e5\u5fd7\u5df2\u5199\u51fa\u3002",
    "table_validation_summary": "\u4fdd\u5b58\u672a\u6210\u529f\uff0c\u8bf7\u5148\u4fee\u6b63\u4ee5\u4e0b\u5355\u5143\u683c\u95ee\u9898\uff1a",
    "manual_panel_title": "\u4eba\u5de5\u4ecb\u5165\u5de5\u4f5c\u53f0",
    "manual_tasks_path": "\u4efb\u52a1\u6587\u4ef6\u8def\u5f84",
    "manual_output_path": "\u56de\u5199\u8f93\u51fa\u8def\u5f84",
    "manual_actor": "\u5904\u7406\u4eba",
    "manual_note": "\u5907\u6ce8",
    "manual_load_button": "\u52a0\u8f7d\u5f85\u5904\u7406\u4efb\u52a1",
    "manual_empty": "\u5f53\u524d\u6ca1\u6709\u5f85\u4eba\u5de5\u4ecb\u5165\u4efb\u52a1\u3002",
    "manual_resolved": "\u5df2\u5904\u7406\u4efb\u52a1 {task_id} -> {status}",
    "manual_decision": "\u5904\u7406\u7ed3\u679c",
    "manual_submit": "\u63d0\u4ea4",
    "manual_readonly_notice": "\u65e7 Excel \u4eba\u5de5\u4ecb\u5165\u94fe\u8def\u5df2\u8fdb\u5165\u53ea\u8bfb\u517c\u5bb9\u72b6\u6001\uff0c\u4e0d\u518d\u5141\u8bb8\u5728\u8fd9\u91cc\u6267\u884c\u6b63\u5f0f\u5904\u7406\u3002\u8bf7\u6539\u7528 SQLite review_tasks \u6216 /runtime \u5165\u53e3\u3002",
    "runtime_panel_title": "SQLite \u8fd0\u884c\u6001\u67e5\u770b",
    "runtime_db_path": "\u8fd0\u884c\u6001\u6570\u636e\u5e93\u8def\u5f84",
    "runtime_load_button": "\u52a0\u8f7d\u8fd0\u884c\u6001\u6570\u636e",
    "runtime_login_title": "\u8fd0\u884c\u6001\u767b\u5f55",
    "runtime_login_user": "\u540e\u53f0\u8d26\u53f7",
    "runtime_login_password": "\u540e\u53f0\u5bc6\u7801",
    "runtime_login_button": "\u767b\u5f55",
    "runtime_logout_button": "\u9000\u51fa\u767b\u5f55",
    "runtime_session_user": "\u5f53\u524d\u767b\u5f55\u8eab\u4efd",
    "runtime_review_detail": "\u590d\u6838\u8be6\u60c5",
    "runtime_review_handle": "\u5904\u7406\u590d\u6838",
    "runtime_history": "\u4efb\u52a1\u72b6\u6001\u5386\u53f2",
    "runtime_history_empty": "\u6682\u65e0\u72b6\u6001\u53d8\u66f4\u5386\u53f2\u3002",
    "runtime_reviewer_code": "\u590d\u6838\u7801\uff08\u8fc7\u6e21\u5b57\u6bb5\uff09",
    "runtime_resolution_note": "\u5904\u7406\u5907\u6ce8",
    "runtime_resolution_payload": "\u7ed3\u679c JSON",
    "runtime_submit_review": "\u63d0\u4ea4\u590d\u6838\u7ed3\u679c",
    "runtime_login_required": "\u8fd0\u884c\u6001\u590d\u6838\u5904\u7406\u9700\u8981\u5148\u767b\u5f55\u3002",
    "runtime_already_handled": "\u8be5\u590d\u6838\u4efb\u52a1\u5df2\u5904\u7406\uff0c\u4e0d\u80fd\u91cd\u590d\u63d0\u4ea4\u3002",
    "runtime_review_resolved": "\u5df2\u5904\u7406\u590d\u6838\u4efb\u52a1 {review_task_id} -> {status}",
    "runtime_source_not_advanced": "\u6e90\u4efb\u52a1\u5f53\u524d\u4e0d\u662f manual_review\uff0c\u672a\u81ea\u52a8\u63a8\u52a8\u72b6\u6001\u3002",
    "runtime_adjusted_followup": "\u590d\u6838\u7ed3\u679c\u4e3a adjusted\uff0c\u539f\u4efb\u52a1\u5df2\u8df3\u8fc7\uff0c\u9700\u540e\u7eed\u751f\u6210\u65b0\u4efb\u52a1\u3002",
    "runtime_pending_only": "\u53ea\u6709 pending \u72b6\u6001\u7684\u590d\u6838\u4efb\u52a1\u53ef\u4ee5\u5904\u7406\u3002",
    "runtime_tasks": "\u8fd0\u884c\u6001\u4efb\u52a1",
    "runtime_reviews": "\u4eba\u5de5\u590d\u6838\u4efb\u52a1",
    "runtime_notifications": "\u901a\u77e5\u8bb0\u5f55",
    "runtime_task_filters": "\u4efb\u52a1\u7b5b\u9009",
    "runtime_review_filters": "\u590d\u6838\u7b5b\u9009",
    "runtime_notification_filters": "\u901a\u77e5\u7b5b\u9009",
    "runtime_filter_apply": "\u5e94\u7528\u7b5b\u9009",
    "runtime_filter_reset": "\u91cd\u7f6e",
    "runtime_notification_detail": "\u901a\u77e5\u8be6\u60c5",
    "runtime_notification_none": "\u6682\u65e0\u7b26\u5408\u6761\u4ef6\u7684\u901a\u77e5\u8bb0\u5f55\u3002",
    "runtime_history_summary": "\u72b6\u6001\u5386\u53f2\u6458\u8981",
}


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    print(f"{UI_TEXT['site_title']} {host}:{port}")
    with make_server(host, port, application) as httpd:
        httpd.serve_forever()


def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/")

    if path == "/health":
        return _respond(start_response, "200 OK", "text/plain; charset=utf-8", "ok")
    if path == "/":
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", _handle_dashboard(method, environ))
    if path == "/tables":
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", _handle_tables(method, environ))
    if path == "/execution":
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", _handle_execution(method, environ))
    if path == "/manual-intervention":
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", _handle_manual_intervention(method, environ))
    if path == "/runtime/login":
        status, body, headers = _handle_runtime_login(environ)
        return _respond(start_response, status, "text/html; charset=utf-8", body, headers=headers)
    if path == "/runtime/logout":
        status, body, headers = _handle_runtime_logout(environ)
        return _respond(start_response, status, "text/html; charset=utf-8", body, headers=headers)
    if path == "/runtime":
        result = _handle_runtime(method, environ)
        if isinstance(result, tuple):
            status, body, headers = result
            return _respond(start_response, status, "text/html; charset=utf-8", body, headers=headers)
        return _respond(start_response, "200 OK", "text/html; charset=utf-8", result)
    return _respond(start_response, "404 Not Found", "text/plain; charset=utf-8", "Not Found")


def _handle_dashboard(method: str, environ) -> str:
    params = default_dashboard_state()
    message = ""
    level = "info"
    validation_summary: ValidationSummary | None = None
    generation_summary: TaskGenerationSummary | None = None
    preview_ready = False

    if method == "POST":
        parsed = _parse_body(environ)
        params = {
            "products": _first(parsed, "products", params["products"]),
            "price_rules": _first(parsed, "price_rules", params["price_rules"]),
            "listing_rules": _first(parsed, "listing_rules", params["listing_rules"]),
            "output": _first(parsed, "output", params["output"]),
            "platform": _first(parsed, "platform", params["platform"]),
            "inventory_strategy": _first(parsed, "inventory_strategy", params["inventory_strategy"]),
            "use_mock_ai": "use_mock_ai" in parsed,
        }
        action = _first(parsed, "action", "validate")

        try:
            workflow_inputs = WorkflowInputs(
                products_path=Path(str(params["products"])),
                price_rules_path=Path(str(params["price_rules"])),
                listing_rules_path=Path(str(params["listing_rules"])),
                output_path=Path(str(params["output"])),
                platform_name=str(params["platform"]),
                inventory_strategy=str(params["inventory_strategy"]),
                use_mock_ai=bool(params["use_mock_ai"]),
            )
            if action == "validate":
                validation_summary = validate_sources(workflow_inputs)
                message = UI_TEXT["validated"]
                level = "success"
            elif action == "preview":
                generation_summary = preview_tasks_from_sources(workflow_inputs)
                validation_summary = generation_summary.validation
                message = UI_TEXT["previewed"].format(count=len(generation_summary.tasks))
                level = "success"
                preview_ready = True
            else:
                generation_summary = generate_tasks_from_sources(workflow_inputs)
                validation_summary = generation_summary.validation
                message = UI_TEXT["generated"].format(
                    count=len(generation_summary.tasks),
                    path=generation_summary.output_path,
                )
                level = "success"
        except (ValidationError, FileNotFoundError) as exc:
            message = str(exc)
            level = "error"

    return render_dashboard_page(
        params=params,
        message=message,
        message_level=level,
        validation_summary=validation_summary,
        generation_summary=generation_summary,
        preview_ready=preview_ready,
    )


def _handle_tables(method: str, environ) -> str:
    params = default_table_editor_state()
    message = ""
    level = "info"
    records: list[dict[str, object]] = []
    table_issues: list[tuple[int, str, str]] = []

    if method == "POST":
        parsed = _parse_body(environ)
        previous_table_name = _first(parsed, "previous_table_name", str(params["table_name"]))
        requested_table = _first(parsed, "table_name", str(params["table_name"]))
        table_name = requested_table if requested_table in TABLE_OPTIONS else "products"
        posted_path = _first(parsed, "table_path", str(params["table_path"]))
        params["table_name"] = table_name
        params["table_path"] = _resolve_table_path(table_name, previous_table_name, posted_path)
        action = _first(parsed, "action", "load")
        headers = get_table_headers(table_name)

        try:
            table_path = Path(str(params["table_path"]))
            if action == "save":
                records = _extract_table_rows(parsed, headers)
                save_table_records(table_name, table_path, records)
                message = UI_TEXT["saved_rows"].format(count=len(records), path=table_path)
                level = "success"

            records = load_table_records(table_name, table_path)
            if action == "load" and not message:
                message = UI_TEXT["loaded_rows"].format(count=len(records))
                level = "success"
        except TableValidationError as exc:
            message = UI_TEXT["table_validation_summary"]
            level = "error"
            table_issues = [(item.row_number, item.field_name, item.message) for item in exc.issues]
            if action == "save":
                records = _extract_table_rows(parsed, headers)
        except (ValidationError, FileNotFoundError) as exc:
            message = str(exc)
            level = "error"
            if action == "save":
                records = _extract_table_rows(parsed, headers)
    else:
        headers = get_table_headers(str(params["table_name"]))
        try:
            records = load_table_records(str(params["table_name"]), Path(str(params["table_path"])))
        except (ValidationError, FileNotFoundError):
            records = []

    headers = get_table_headers(str(params["table_name"]))
    return render_table_editor_page(
        params=params,
        headers=headers,
        records=records,
        message=message,
        message_level=level,
        table_issues=table_issues,
    )


def _handle_execution(method: str, environ) -> str:
    params = default_execution_state()
    message = ""
    level = "info"
    execution_summary: ExecutionSimulationSummary | None = None

    if method == "POST":
        parsed = _parse_body(environ)
        params = {
            "tasks_path": _first(parsed, "tasks_path", params["tasks_path"]),
            "logs_output": _first(parsed, "logs_output", params["logs_output"]),
            "updated_tasks_output": _first(parsed, "updated_tasks_output", params["updated_tasks_output"]),
            "executor_name": _first(parsed, "executor_name", params["executor_name"]),
        }
        try:
            execution_summary = simulate_execution_from_tasks(
                ExecutionSimulationInputs(
                    tasks_path=Path(str(params["tasks_path"])),
                    logs_output_path=Path(str(params["logs_output"])),
                    updated_tasks_output_path=Path(str(params["updated_tasks_output"]))
                    if str(params["updated_tasks_output"]).strip()
                    else None,
                    executor_name=str(params["executor_name"]),
                )
            )
            message = UI_TEXT["execution_done"].format(count=len(execution_summary.tasks))
            level = "success"
        except (ValidationError, FileNotFoundError) as exc:
            message = str(exc)
            level = "error"

    return render_execution_page(
        params=params,
        message=message,
        message_level=level,
        execution_summary=execution_summary,
    )


def _handle_manual_intervention(method: str, environ) -> str:
    params = default_manual_state()
    message = UI_TEXT["manual_readonly_notice"]
    level = "info"
    tasks = []

    if method == "POST":
        parsed = _parse_body(environ)
        params = {
            "tasks_path": _first(parsed, "tasks_path", params["tasks_path"]),
            "output_path": _first(parsed, "output_path", params["output_path"]),
            "actor": _first(parsed, "actor", params["actor"]),
            "note": _first(parsed, "note", ""),
        }
        try:
            tasks = list_manual_intervention_tasks(Path(str(params["tasks_path"])))
            if _first(parsed, "action", "load") == "resolve":
                message = "旧 Excel 人工介入入口已弃用，不能再执行正式处理。请改用 SQLite review_tasks 或 /runtime 入口。"
                level = "error"
            elif not tasks:
                message = UI_TEXT["manual_empty"]
                level = "info"
        except (ValidationError, FileNotFoundError) as exc:
            message = str(exc)
            level = "error"

    return render_manual_intervention_page(
        params=params,
        message=message,
        message_level=level,
        tasks=tasks,
    )


def _handle_runtime(method: str, environ) -> str | tuple[str, str, list[tuple[str, str]]]:
    params = default_runtime_state()
    query = _parse_query(environ)
    params["runtime_db"] = _first(query, "runtime_db", params["runtime_db"])
    params["review_task_id"] = _first(query, "review_task_id", "")
    params["task_id"] = _first(query, "task_id", "")
    params["notification_id"] = _first(query, "notification_id", "")
    params["task_trade_date"] = _first(query, "task_trade_date", "")
    params["task_status"] = _first(query, "task_status", "")
    params["review_trade_date"] = _first(query, "review_trade_date", "")
    params["review_status"] = _first(query, "review_status", "")
    params["notification_related_review_task_id"] = _first(query, "notification_related_review_task_id", "")
    params["notification_send_status"] = _first(query, "notification_send_status", "")
    message = _first(query, "message", "")
    level = _first(query, "level", "info" if message else "info")
    tasks = []
    reviews = []
    notifications = []
    selected_review = None
    selected_task = None
    selected_notification = None
    history = []
    session_user = _get_runtime_session_user(environ)
    filters = _runtime_filter_state(params)

    if method == "POST":
        if session_user is None:
            return _redirect_response(
                _build_runtime_url(
                    params["runtime_db"],
                    **filters,
                    message=UI_TEXT["runtime_login_required"],
                    level="error",
                )
            )
        parsed = _parse_body(environ)
        params["runtime_db"] = _first(parsed, "runtime_db", params["runtime_db"])
        params["review_task_id"] = _first(parsed, "review_task_id", params["review_task_id"])
        params["task_id"] = _first(parsed, "task_id", params["task_id"])
        params["notification_id"] = _first(parsed, "notification_id", params["notification_id"])
        params["task_trade_date"] = _first(parsed, "task_trade_date", params["task_trade_date"])
        params["task_status"] = _first(parsed, "task_status", params["task_status"])
        params["review_trade_date"] = _first(parsed, "review_trade_date", params["review_trade_date"])
        params["review_status"] = _first(parsed, "review_status", params["review_status"])
        params["notification_related_review_task_id"] = _first(
            parsed,
            "notification_related_review_task_id",
            params["notification_related_review_task_id"],
        )
        params["notification_send_status"] = _first(parsed, "notification_send_status", params["notification_send_status"])
        filters = _runtime_filter_state(params)
        action = _first(parsed, "action", "load")
        if action == "resolve_review":
            try:
                db_path = Path(str(params["runtime_db"]))
                review = get_runtime_review_task(db_path, params["review_task_id"])
                if review is None:
                    raise ValidationError(f"review task not found: {params['review_task_id']}")
                if review.review_status != ReviewTaskStatus.PENDING:
                    raise ValidationError(UI_TEXT["runtime_already_handled"])

                resolution_payload = _parse_resolution_payload(
                    _first(parsed, "resolution_payload_json", "{}")
                )
                reviewer_code = _first(parsed, "reviewer_code", "").strip()
                if reviewer_code:
                    resolution_payload["reviewer_code"] = reviewer_code

                review_status = ReviewTaskStatus(_first(parsed, "review_status", ReviewTaskStatus.APPROVED.value))
                source_task_status = None
                source_followup_message = ""
                if review.source_task_id:
                    source_task = get_runtime_task(db_path, review.source_task_id)
                    if source_task is not None and source_task.task_status == TaskStatus.MANUAL_REVIEW:
                        source_task_status = _map_review_to_source_task_status(review_status)
                    else:
                        source_followup_message = UI_TEXT["runtime_source_not_advanced"]
                    params["task_id"] = review.source_task_id

                resolved = resolve_runtime_review_task(
                    RuntimeReviewResolutionInputs(
                        db_path=db_path,
                        review_task_id=review.review_task_id,
                        status=review_status,
                        actor=session_user,
                        actor_source="session_user",
                        note=_first(parsed, "resolution_note", ""),
                        source_task_status=source_task_status,
                        resolution_payload=resolution_payload,
                    )
                )
                success_message = UI_TEXT["runtime_review_resolved"].format(
                    review_task_id=resolved.review_task_id,
                    status=resolved.review_status.value,
                )
                if review_status == ReviewTaskStatus.ADJUSTED:
                    success_message = f"{success_message} {UI_TEXT['runtime_adjusted_followup']}"
                if source_followup_message:
                    success_message = f"{success_message} {source_followup_message}"
                return _redirect_response(
                    _build_runtime_url(
                        params["runtime_db"],
                        review_task_id=resolved.review_task_id,
                        task_id=params["task_id"],
                        notification_related_review_task_id=filters["notification_related_review_task_id"],
                        notification_send_status=filters["notification_send_status"],
                        task_trade_date=filters["task_trade_date"],
                        task_status=filters["task_status"],
                        review_trade_date=filters["review_trade_date"],
                        review_status=filters["review_status"],
                        message=success_message,
                        level="success",
                    )
                )
            except (ValidationError, FileNotFoundError) as exc:
                message = str(exc)
                level = "error"

    if session_user is not None:
        try:
            db_path = Path(str(params["runtime_db"]))
            tasks = list_runtime_tasks(
                db_path,
                trade_date=parse_date(params["task_trade_date"], "task_trade_date") if params["task_trade_date"] else None,
                status=TaskStatus(params["task_status"]) if params["task_status"] else None,
            )
            reviews = list_runtime_review_tasks(
                db_path,
                trade_date=parse_date(params["review_trade_date"], "review_trade_date") if params["review_trade_date"] else None,
                status=ReviewTaskStatus(params["review_status"]) if params["review_status"] else None,
            )
            notifications = list_runtime_notification_logs(
                db_path,
                related_review_task_id=params["notification_related_review_task_id"] or None,
                send_status=params["notification_send_status"] or None,
            )
            if params["review_task_id"]:
                selected_review = get_runtime_review_task(db_path, params["review_task_id"])
                if selected_review is not None and not params["task_id"] and selected_review.source_task_id:
                    params["task_id"] = selected_review.source_task_id
            if params["task_id"]:
                selected_task = get_runtime_task(db_path, params["task_id"])
                if selected_task is not None:
                    history = list_runtime_task_history(db_path, selected_task.task_id)
            if params["notification_id"]:
                selected_notification = get_runtime_notification_log(db_path, params["notification_id"])
        except (ValidationError, FileNotFoundError) as exc:
            message = str(exc)
            level = "error"
    elif not message:
        message = UI_TEXT["runtime_login_required"]

    return render_runtime_page(
        params=params,
        message=message,
        message_level=level,
        tasks=tasks,
        reviews=reviews,
        notifications=notifications,
        session_user=session_user,
        selected_review=selected_review,
        selected_task=selected_task,
        selected_notification=selected_notification,
        task_history=history,
    )


def _handle_runtime_login(environ) -> tuple[str, str, list[tuple[str, str]]]:
    parsed = _parse_body(environ)
    runtime_db = _first(parsed, "runtime_db", str(DEFAULT_RUNTIME_DB))
    username = _first(parsed, "username", _runtime_admin_user()).strip() or _runtime_admin_user()
    password = _first(parsed, "password", "")
    redirect_target = _build_runtime_url(runtime_db)

    expected_password = os.getenv("RUNTIME_ADMIN_PASSWORD", "")
    if expected_password and username == _runtime_admin_user() and password == expected_password:
        return _redirect_response(
            redirect_target,
            headers=_session_cookie_headers(_runtime_admin_user()),
        )
    if _dev_mode():
        shared_password = os.getenv("RUNTIME_DEV_SHARED_PASSWORD", "")
        if shared_password and password == shared_password:
            return _redirect_response(
                redirect_target,
                headers=_session_cookie_headers("dev_shared_user"),
            )

    message = "登录失败：账号或密码不正确。"
    if not expected_password and not (_dev_mode() and os.getenv("RUNTIME_DEV_SHARED_PASSWORD", "")):
        message = "尚未配置 RUNTIME_ADMIN_PASSWORD，无法进行运行态复核登录。"
    body = render_runtime_page(
        params={
            "runtime_db": runtime_db,
            "review_task_id": "",
            "task_id": "",
            "notification_id": "",
            "task_trade_date": "",
            "task_status": "",
            "review_trade_date": "",
            "review_status": "",
            "notification_related_review_task_id": "",
            "notification_send_status": "",
        },
        message=message,
        message_level="error",
        tasks=[],
        reviews=[],
        notifications=[],
        session_user=None,
        selected_review=None,
        selected_task=None,
        selected_notification=None,
        task_history=[],
    )
    return ("200 OK", body, [])


def _handle_runtime_logout(environ) -> tuple[str, str, list[tuple[str, str]]]:
    parsed = _parse_body(environ)
    runtime_db = _first(parsed, "runtime_db", str(DEFAULT_RUNTIME_DB))
    cookie = SimpleCookie()
    cookie.load(environ.get("HTTP_COOKIE", ""))
    session_cookie = cookie.get(RUNTIME_SESSION_COOKIE)
    if session_cookie is not None:
        _RUNTIME_SESSIONS.pop(session_cookie.value, None)
    return _redirect_response(
        _build_runtime_url(runtime_db, message="已退出运行态登录。", level="success"),
        headers=_clear_session_cookie_headers(),
    )


def _resolve_table_path(table_name: str, previous_table_name: str, posted_path: str) -> str:
    previous_default = str(TABLE_OPTIONS.get(previous_table_name, TABLE_OPTIONS["products"])["path"])
    current_default = str(TABLE_OPTIONS[table_name]["path"])
    if not posted_path.strip():
        return current_default
    normalized_posted = os.path.normcase(os.path.normpath(posted_path))
    normalized_previous_default = os.path.normcase(os.path.normpath(previous_default))
    if table_name != previous_table_name and normalized_posted == normalized_previous_default:
        return current_default
    return posted_path


def default_dashboard_state() -> dict[str, str | bool]:
    return {
        "products": str(DEFAULT_PRODUCTS),
        "price_rules": str(DEFAULT_PRICE_RULES),
        "listing_rules": str(DEFAULT_LISTING_RULES),
        "output": str(DEFAULT_OUTPUT),
        "platform": "default_platform",
        "inventory_strategy": "conservative_v1",
        "use_mock_ai": True,
    }


def default_table_editor_state() -> dict[str, str]:
    return {
        "table_name": "products",
        "table_path": str(DEFAULT_PRODUCTS),
    }


def default_execution_state() -> dict[str, str]:
    return {
        "tasks_path": str(DEFAULT_OUTPUT),
        "logs_output": str(DEFAULT_EXECUTION_LOGS),
        "updated_tasks_output": str(DEFAULT_EXECUTED_TASKS),
        "executor_name": "mock_executor",
    }


def default_manual_state() -> dict[str, str]:
    return {
        "tasks_path": str(DEFAULT_MANUAL_TASKS),
        "output_path": str(DEFAULT_MANUAL_TASKS),
        "actor": "manual_operator",
        "note": "",
    }


def default_runtime_state() -> dict[str, str]:
    return {
        "runtime_db": str(DEFAULT_RUNTIME_DB),
        "review_task_id": "",
        "task_id": "",
        "notification_id": "",
        "task_trade_date": "",
        "task_status": "",
        "review_trade_date": "",
        "review_status": "",
        "notification_related_review_task_id": "",
        "notification_send_status": "",
    }


def render_dashboard_page(
    *,
    params: dict[str, str | bool],
    message: str,
    message_level: str,
    validation_summary: ValidationSummary | None,
    generation_summary: TaskGenerationSummary | None,
    preview_ready: bool,
) -> str:
    summary_html = ""
    if validation_summary is not None:
        summary_html = f"""
        <section class="panel">
          <h2>{escape(UI_TEXT["data_summary"])}</h2>
          <div class="metrics">
            <div class="metric"><span class="label">products</span><strong>{len(validation_summary.products)}</strong></div>
            <div class="metric"><span class="label">price_rules</span><strong>{validation_summary.price_rules_count}</strong></div>
            <div class="metric"><span class="label">listing_rules</span><strong>{validation_summary.listing_rules_count}</strong></div>
          </div>
        </section>
        """

    tasks_html = ""
    if generation_summary is not None:
        confirm_html = ""
        output_hint_label = UI_TEXT["output_file"] if generation_summary.output_written else UI_TEXT["planned_output_file"]
        output_hint_value = generation_summary.output_path if generation_summary.output_written else params["output"]
        rows = []
        for task in generation_summary.tasks[:12]:
            rows.append(
                "<tr>"
                f"<td>{escape(task.internal_sku)}</td>"
                f"<td>{escape(task.action_type.value)}</td>"
                f"<td>{escape(task.task_status.value)}</td>"
                f"<td>{escape(task.platform_name)}</td>"
                f"<td>{escape(str(task.target_price) if task.target_price is not None else '-')}</td>"
                f"<td>{escape(task.pricing_source.value if task.pricing_source else '-')}</td>"
                "</tr>"
            )
        rows_html = "".join(rows) or f"<tr><td colspan='6'>{escape(UI_TEXT['no_tasks'])}</td></tr>"
        task_counts = "".join(
            f"<div class='metric'><span class='label'>{escape(name)}</span><strong>{count}</strong></div>"
            for name, count in generation_summary.task_counts.items()
        )
        if preview_ready:
            mock_ai_hidden = "<input type='hidden' name='use_mock_ai' value='on'>" if params["use_mock_ai"] else ""
            confirm_html = f"""
          <div class="confirm-box">
            <p>{escape(UI_TEXT["preview_ready"])}</p>
            <form method="post" class="actions">
              <input type="hidden" name="products" value="{escape(str(params["products"]))}">
              <input type="hidden" name="price_rules" value="{escape(str(params["price_rules"]))}">
              <input type="hidden" name="listing_rules" value="{escape(str(params["listing_rules"]))}">
              <input type="hidden" name="output" value="{escape(str(params["output"]))}">
              <input type="hidden" name="platform" value="{escape(str(params["platform"]))}">
              <input type="hidden" name="inventory_strategy" value="{escape(str(params["inventory_strategy"]))}">
              {mock_ai_hidden}
              <button class="primary" type="submit" name="action" value="confirm_generate">{escape(UI_TEXT["confirm_button"])}</button>
            </form>
          </div>
        """
        tasks_html = f"""
        <section class="panel">
          <h2>{escape(UI_TEXT["task_result"])}</h2>
          <div class="metrics">{task_counts}</div>
          <p class="subtle">{escape(output_hint_label)}: {escape(str(output_hint_value))}</p>
          {confirm_html}
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>SKU</th>
                  <th>action</th>
                  <th>status</th>
                  <th>platform</th>
                  <th>target_price</th>
                  <th>pricing_source</th>
                </tr>
              </thead>
              <tbody>{rows_html}</tbody>
            </table>
          </div>
        </section>
        """

    checked = "checked" if params["use_mock_ai"] else ""
    inventory_strategy_options = [
        ("conservative_v1", UI_TEXT["inventory_strategy_conservative"]),
        ("balanced_v1", UI_TEXT["inventory_strategy_balanced"]),
    ]
    inventory_strategy_html = "".join(
        (
            f"<option value='{escape(value)}' {'selected' if params['inventory_strategy'] == value else ''}>"
            f"{escape(label)}</option>"
        )
        for value, label in inventory_strategy_options
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["site_title"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell">
    {_hero(UI_TEXT["dashboard_title"], UI_TEXT["dashboard_lede"])}
    {navigation("/")}
    {_banner(message, message_level)}
    <div class="layout">
      <section class="panel">
        <h2>{escape(UI_TEXT["task_panel_title"])}</h2>
        <form method="post" class="grid">
          <div class="field">
            <label for="products">{escape(UI_TEXT["products_path"])}</label>
            <input id="products" name="products" type="text" value="{escape(str(params["products"]))}">
          </div>
          <div class="field">
            <label for="price_rules">{escape(UI_TEXT["price_rules_path"])}</label>
            <input id="price_rules" name="price_rules" type="text" value="{escape(str(params["price_rules"]))}">
          </div>
          <div class="field">
            <label for="listing_rules">{escape(UI_TEXT["listing_rules_path"])}</label>
            <input id="listing_rules" name="listing_rules" type="text" value="{escape(str(params["listing_rules"]))}">
          </div>
          <div class="field">
            <label for="output">{escape(UI_TEXT["output_path"])}</label>
            <input id="output" name="output" type="text" value="{escape(str(params["output"]))}">
          </div>
          <div class="field">
            <label for="platform">{escape(UI_TEXT["platform_name"])}</label>
            <input id="platform" name="platform" type="text" value="{escape(str(params["platform"]))}">
          </div>
          <div class="field">
            <label for="inventory_strategy">{escape(UI_TEXT["inventory_strategy"])}</label>
            <select id="inventory_strategy" name="inventory_strategy">{inventory_strategy_html}</select>
          </div>
          <label class="checkbox">
            <input name="use_mock_ai" type="checkbox" {checked}>
            {escape(UI_TEXT["use_mock_ai"])}
          </label>
          <div class="actions">
            <button class="secondary" type="submit" name="action" value="validate">{escape(UI_TEXT["validate_button"])}</button>
            <button class="primary" type="submit" name="action" value="preview">{escape(UI_TEXT["preview_button"])}</button>
          </div>
        </form>
      </section>

      <section class="panel">
        <h2>{escape(UI_TEXT["resources_title"])}</h2>
        <div class="aside-list">
          <div>{escape(UI_TEXT["products_path"])}<code>{escape(str(DEFAULT_PRODUCTS))}</code></div>
          <div>{escape(UI_TEXT["price_rules_path"])}<code>{escape(str(DEFAULT_PRICE_RULES))}</code></div>
          <div>{escape(UI_TEXT["listing_rules_path"])}<code>{escape(str(DEFAULT_LISTING_RULES))}</code></div>
          <div>{escape(UI_TEXT["output_path"])}<code>{escape(str(DEFAULT_OUTPUT))}</code></div>
        </div>
      </section>
    </div>
    {summary_html}
    {tasks_html}
  </main>
</body>
</html>
"""


def render_table_editor_page(
    *,
    params: dict[str, str],
    headers: list[str],
    records: list[dict[str, object]],
    message: str,
    message_level: str,
    table_issues: list[tuple[int, str, str]],
) -> str:
    issue_map = {(row_number - 2, field_name): detail for row_number, field_name, detail in table_issues}
    issues_html = ""
    if table_issues:
        issue_items = "".join(
            f"<li>\u7b2c {row_number} \u884c {escape(TABLE_HEADER_LABELS.get(field_name, field_name))}: {escape(detail)}</li>"
            for row_number, field_name, detail in table_issues
        )
        issues_html = f"<ul class='issue-list'>{issue_items}</ul>"

    rows = records + [{header: "" for header in headers} for _ in range(3)]
    row_html: list[str] = []
    for row_index, row in enumerate(rows):
        cells = []
        for header in headers:
            value = "" if row.get(header) is None else str(row.get(header))
            issue_detail = issue_map.get((row_index, header), "")
            issue_class = " cell-input invalid" if issue_detail else " cell-input"
            issue_note = f"<div class='cell-issue'>{escape(issue_detail)}</div>" if issue_detail else ""
            cells.append(
                f"<td><input class='{issue_class.strip()}' type='text' name='cell__{row_index}__{header}' value='{escape(value)}'>{issue_note}</td>"
            )
        row_html.append(f"<tr><td class='row-index'>{row_index + 1}</td>{''.join(cells)}</tr>")

    options_html = "".join(
        f"<option value='{name}' data-default-path='{escape(str(meta['path']))}' {'selected' if params['table_name'] == name else ''}>{escape(str(meta['label']))}</option>"
        for name, meta in TABLE_OPTIONS.items()
    )
    header_html = "".join(
        f"<th title='{escape(header)}'>{escape(TABLE_HEADER_LABELS.get(header, header))}</th>"
        for header in headers
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["tables_tab"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell wide-shell">
    {_hero(UI_TEXT["table_editor_title"], UI_TEXT["table_editor_lede"])}
    {navigation("/tables")}
    {_banner(message, message_level)}
    <section class="panel">
      <h2>{escape(UI_TEXT["table_picker"])}</h2>
      <form method="post" class="grid two-col">
        <input type="hidden" name="previous_table_name" value="{escape(params['table_name'])}">
        <div class="field">
          <label for="table_name">{escape(UI_TEXT["table_type"])}</label>
          <select id="table_name" name="table_name">{options_html}</select>
        </div>
        <div class="field">
          <label for="table_path">{escape(UI_TEXT["table_path"])}</label>
          <input id="table_path" name="table_path" type="text" value="{escape(params['table_path'])}">
        </div>
        <div class="actions">
          <button class="secondary" type="submit" name="action" value="load">{escape(UI_TEXT["load_button"])}</button>
          <button class="primary" type="submit" name="action" value="save">{escape(UI_TEXT["save_button"])}</button>
        </div>
      </form>
    </section>

    <section class="panel">
      <h2>{escape(str(TABLE_OPTIONS[params['table_name']]['label']))}</h2>
      <p class="subtle">{escape(UI_TEXT["table_hint"])}</p>
      {issues_html}
      <form method="post">
        <input type="hidden" name="previous_table_name" value="{escape(params['table_name'])}">
        <input type="hidden" name="table_name" value="{escape(params['table_name'])}">
        <input type="hidden" name="table_path" value="{escape(params['table_path'])}">
        <div class="table-wrap">
          <table class="editor-table">
            <thead>
              <tr>
                <th>#</th>
                {header_html}
              </tr>
            </thead>
            <tbody>
              {''.join(row_html)}
            </tbody>
          </table>
        </div>
        <div class="actions sticky-actions">
          <button class="primary" type="submit" name="action" value="save">{escape(UI_TEXT["save_button"])}</button>
        </div>
      </form>
    </section>
  </main>
  <script>
    const tableNameSelect = document.getElementById("table_name");
    const tablePathInput = document.getElementById("table_path");
    if (tableNameSelect && tablePathInput) {{
      tableNameSelect.addEventListener("change", () => {{
        const option = tableNameSelect.options[tableNameSelect.selectedIndex];
        const defaultPath = option.getAttribute("data-default-path");
        if (defaultPath) {{
          tablePathInput.value = defaultPath;
        }}
      }});
    }}
  </script>
</body>
</html>
"""


def render_execution_page(
    *,
    params: dict[str, str],
    message: str,
    message_level: str,
    execution_summary: ExecutionSimulationSummary | None,
) -> str:
    result_html = ""
    if execution_summary is not None:
        rows_html = "".join(
            "<tr>"
            f"<td>{escape(log.task_id)}</td>"
            f"<td>{escape(log.executor_name)}</td>"
            f"<td>{escape('success' if log.success_flag else 'failed')}</td>"
            f"<td>{escape(log.raw_output)}</td>"
            "</tr>"
            for log in execution_summary.logs[:12]
        )
        result_html = f"""
        <section class="panel">
          <h2>{escape(UI_TEXT["execution_result"])}</h2>
          <div class="metrics">
            <div class="metric"><span class="label">logs</span><strong>{len(execution_summary.logs)}</strong></div>
            <div class="metric"><span class="label">success</span><strong>{execution_summary.success_count}</strong></div>
          </div>
          <p class="subtle">{escape(UI_TEXT["execution_logs_file"])}: {escape(str(execution_summary.logs_output_path))}</p>
          <p class="subtle">{escape(UI_TEXT["execution_updated_tasks_file"])}: {escape(str(execution_summary.updated_tasks_output_path or '-'))}</p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>task_id</th>
                  <th>executor</th>
                  <th>result</th>
                  <th>raw_output</th>
                </tr>
              </thead>
              <tbody>{rows_html}</tbody>
            </table>
          </div>
        </section>
        """

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["execution_tab"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell">
    {_hero(UI_TEXT["execution_panel_title"], UI_TEXT["dashboard_lede"])}
    {navigation("/execution")}
    {_banner(message, message_level)}
    <section class="panel">
      <h2>{escape(UI_TEXT["execution_panel_title"])}</h2>
      <form method="post" class="grid">
        <div class="field">
          <label for="tasks_path">{escape(UI_TEXT["execution_source_path"])}</label>
          <input id="tasks_path" name="tasks_path" type="text" value="{escape(params["tasks_path"])}">
        </div>
        <div class="field">
          <label for="logs_output">{escape(UI_TEXT["execution_logs_path"])}</label>
          <input id="logs_output" name="logs_output" type="text" value="{escape(params["logs_output"])}">
        </div>
        <div class="field">
          <label for="updated_tasks_output">{escape(UI_TEXT["execution_tasks_path"])}</label>
          <input id="updated_tasks_output" name="updated_tasks_output" type="text" value="{escape(params["updated_tasks_output"])}">
        </div>
        <div class="field">
          <label for="executor_name">{escape(UI_TEXT["executor_name"])}</label>
          <input id="executor_name" name="executor_name" type="text" value="{escape(params["executor_name"])}">
        </div>
        <div class="actions">
          <button class="primary" type="submit">{escape(UI_TEXT["simulate_button"])}</button>
        </div>
      </form>
    </section>
    {result_html}
  </main>
</body>
</html>
"""


def render_manual_intervention_page(
    *,
    params: dict[str, str],
    message: str,
    message_level: str,
    tasks,
) -> str:
    rows_html = "".join(
        "<tr>"
        f"<td>{escape(task.task_id)}</td>"
        f"<td>{escape(task.internal_sku or '-')}</td>"
        f"<td>{escape(task.action_type.value)}</td>"
        f"<td>{escape(task.task_status.value)}</td>"
        f"<td>{escape(task.result_message or '-')}</td>"
        f"<td>{escape(str(task.required_by) if task.required_by is not None else '-')}</td>"
        f"<td>{escape(UI_TEXT['manual_readonly_notice'])}</td>"
        "</tr>"
        for task in tasks
    ) or f"<tr><td colspan='7'>{escape(UI_TEXT['manual_empty'])}</td></tr>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["manual_tab"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell wide-shell">
    {_hero(UI_TEXT["manual_panel_title"], UI_TEXT["dashboard_lede"])}
    {navigation("/manual-intervention")}
    {_banner(message, message_level)}
    <section class="panel">
      <h2>{escape(UI_TEXT["manual_panel_title"])}</h2>
      <p class="subtle">{escape(UI_TEXT["manual_readonly_notice"])}</p>
      <form method="post" class="grid two-col">
        <div class="field">
          <label for="tasks_path">{escape(UI_TEXT["manual_tasks_path"])}</label>
          <input id="tasks_path" name="tasks_path" type="text" value="{escape(params["tasks_path"])}">
        </div>
        <div class="field">
          <label for="output_path">{escape(UI_TEXT["manual_output_path"])}</label>
          <input id="output_path" name="output_path" type="text" value="{escape(params["output_path"])}">
        </div>
        <div class="field">
          <label for="actor">{escape(UI_TEXT["manual_actor"])}</label>
          <input id="actor" name="actor" type="text" value="{escape(params["actor"])}">
        </div>
        <div class="field">
          <label for="note">{escape(UI_TEXT["manual_note"])}</label>
          <input id="note" name="note" type="text" value="{escape(params["note"])}">
        </div>
        <div class="actions">
          <button class="secondary" type="submit" name="action" value="load">{escape(UI_TEXT["manual_load_button"])}</button>
        </div>
      </form>
    </section>
    <section class="panel">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>task_id</th>
              <th>SKU</th>
              <th>action</th>
              <th>status</th>
              <th>message</th>
              <th>required_by</th>
              <th>mode</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </section>
  </main>
</body>
</html>
"""


def render_runtime_page(
    *,
    params: dict[str, str],
    message: str,
    message_level: str,
    tasks,
    reviews,
    notifications,
    session_user: str | None,
    selected_review,
    selected_task,
    task_history,
) -> str:
    if session_user is None:
        login_panel = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_login_title"])}</h2>
      <form method="post" action="/runtime/login" class="grid two-col">
        <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
        <div class="field">
          <label for="runtime_username">{escape(UI_TEXT["runtime_login_user"])}</label>
          <input id="runtime_username" name="username" type="text" value="{escape(_runtime_admin_user())}">
        </div>
        <div class="field">
          <label for="runtime_password">{escape(UI_TEXT["runtime_login_password"])}</label>
          <input id="runtime_password" name="password" type="password" value="">
        </div>
        <div class="actions">
          <button class="primary" type="submit">{escape(UI_TEXT["runtime_login_button"])}</button>
        </div>
      </form>
    </section>
"""
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["runtime_tab"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell wide-shell">
    {_hero(UI_TEXT["runtime_panel_title"], UI_TEXT["dashboard_lede"])}
    {navigation("/runtime")}
    {_banner(message, message_level)}
    {login_panel}
  </main>
</body>
</html>
"""

    runtime_query = _build_runtime_query(
        params["runtime_db"],
        review_task_id=params.get("review_task_id", ""),
        task_id=params.get("task_id", ""),
    )
    task_rows = "".join(
        "<tr>"
        f"<td><a href='/runtime?{escape(_build_runtime_query(params['runtime_db'], task_id=task.task_id, review_task_id=params.get('review_task_id', '')))}'>{escape(task.task_id)}</a></td>"
        f"<td>{escape(task.trade_date.isoformat() if task.trade_date else '-')}</td>"
        f"<td>{escape(task.scope_type)}:{escape(task.scope_key)}</td>"
        f"<td>{escape(task.action_type.value)}</td>"
        f"<td>{escape(task.task_status.value)}</td>"
        f"<td>{escape(task.internal_sku or '-')}</td>"
        f"<td>{escape(task.platform_name or '-')}</td>"
        "</tr>"
        for task in tasks[:100]
    ) or "<tr><td colspan='7'>-</td></tr>"
    review_rows = "".join(
        "<tr>"
        f"<td><a href='/runtime?{escape(_build_runtime_query(params['runtime_db'], review_task_id=review.review_task_id, task_id=review.source_task_id or ''))}'>{escape(review.review_task_id)}</a></td>"
        f"<td>{escape(review.trade_date.isoformat() if review.trade_date else '-')}</td>"
        f"<td>{escape(review.scope_type)}:{escape(review.scope_key)}</td>"
        f"<td>{escape(review.review_type)}</td>"
        f"<td>{escape(review.review_status.value)}</td>"
        f"<td>{escape(review.source_task_id or '-')}</td>"
        f"<td>{escape(review.reason)}</td>"
        "</tr>"
        for review in reviews[:100]
    ) or "<tr><td colspan='7'>-</td></tr>"
    notification_rows = "".join(
        "<tr>"
        f"<td>{escape(log.notification_id)}</td>"
        f"<td>{escape(log.related_task_id or '-')}</td>"
        f"<td>{escape(log.related_review_task_id or '-')}</td>"
        f"<td>{escape(log.recipient_type)}:{escape(log.recipient)}</td>"
        f"<td>{escape(log.channel)}</td>"
        f"<td>{escape(log.send_status)}</td>"
        f"<td>{escape(log.sent_at.isoformat() if log.sent_at else '-')}</td>"
        f"<td>{escape(log.message)}</td>"
        "</tr>"
        for log in notifications[:100]
    ) or "<tr><td colspan='8'>-</td></tr>"

    selected_review_html = ""
    if selected_review is not None:
        next_source_status = (
            "approved->pending / rejected->skipped / adjusted->skipped / cancelled->cancelled"
            if selected_review.source_task_id
            else "-"
        )
        details = [
            ("review_task_id", selected_review.review_task_id),
            ("review_type", selected_review.review_type),
            ("review_status", selected_review.review_status.value),
            ("scope", f"{selected_review.scope_type}:{selected_review.scope_key}"),
            ("source_task_id", selected_review.source_task_id or "-"),
            ("required_by", selected_review.required_by.isoformat() if selected_review.required_by else "-"),
            ("reason", selected_review.reason or "-"),
            ("resolved_by", selected_review.resolved_by or "-"),
            ("resolved_at", selected_review.resolved_at.isoformat() if selected_review.resolved_at else "-"),
        ]
        detail_rows = "".join(
            f"<tr><th>{escape(label)}</th><td>{escape(str(value))}</td></tr>" for label, value in details
        )
        resolution_pre = escape(_to_pretty_json(selected_review.resolution_payload))
        review_payload_pre = escape(_to_pretty_json(selected_review.review_payload))
        related_notifications = [
            log for log in notifications if log.related_review_task_id == selected_review.review_task_id
        ]
        related_notification_rows = "".join(
            "<tr>"
            f"<td>{escape(log.notification_id)}</td>"
            f"<td>{escape(log.recipient_type)}:{escape(log.recipient)}</td>"
            f"<td>{escape(log.channel)}</td>"
            f"<td>{escape(log.send_status)}</td>"
            f"<td>{escape(log.sent_at.isoformat() if log.sent_at else '-')}</td>"
            f"<td>{escape(log.message)}</td>"
            "</tr>"
            for log in related_notifications
        ) or "<tr><td colspan='6'>-</td></tr>"
        selected_review_html = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_review_detail"])}</h2>
      <div class="table-wrap">
        <table>
          <tbody>{detail_rows}</tbody>
        </table>
      </div>
      <div class="grid two-col">
        <div class="field">
          <label>review_payload_json</label>
          <pre>{review_payload_pre}</pre>
        </div>
        <div class="field">
          <label>resolution_payload_json</label>
          <pre>{resolution_pre}</pre>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>notification_id</th><th>recipient</th><th>channel</th><th>send_status</th><th>sent_at</th><th>message</th></tr></thead>
          <tbody>{related_notification_rows}</tbody>
        </table>
      </div>
    </section>
"""
        if selected_review.review_status == ReviewTaskStatus.PENDING:
            selected_review_html += f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_review_handle"])}</h2>
      <form method="post" action="/runtime" class="grid">
        <input type="hidden" name="action" value="resolve_review">
        <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
        <input type="hidden" name="review_task_id" value="{escape(selected_review.review_task_id)}">
        <input type="hidden" name="task_id" value="{escape(selected_review.source_task_id or '')}">
        <div class="field">
          <label for="review_status">review_status</label>
          <select id="review_status" name="review_status">
            <option value="approved">approved</option>
            <option value="rejected">rejected</option>
            <option value="adjusted">adjusted</option>
            <option value="cancelled">cancelled</option>
          </select>
        </div>
        <div class="field">
          <label>{escape(UI_TEXT["runtime_session_user"])}</label>
          <input type="text" value="{escape(session_user)}" disabled>
        </div>
        <div class="field">
          <label for="reviewer_code">{escape(UI_TEXT["runtime_reviewer_code"])}</label>
          <input id="reviewer_code" name="reviewer_code" type="text" value="">
        </div>
        <div class="field">
          <label for="resolution_note">{escape(UI_TEXT["runtime_resolution_note"])}</label>
          <input id="resolution_note" name="resolution_note" type="text" value="">
        </div>
        <div class="field">
          <label for="resolution_payload_json">{escape(UI_TEXT["runtime_resolution_payload"])}</label>
          <textarea id="resolution_payload_json" name="resolution_payload_json" rows="8">{{}}</textarea>
        </div>
        <p class="subtle">source_task_status: {escape(next_source_status)}（仅当源任务当前处于 manual_review 时自动推动）</p>
        <div class="actions">
          <button class="primary" type="submit">{escape(UI_TEXT["runtime_submit_review"])}</button>
        </div>
      </form>
    </section>
"""

    history_rows = "".join(
        "<tr>"
        f"<td>{escape(item.changed_at.isoformat())}</td>"
        f"<td>{escape(item.from_status.value if item.from_status else '-')}</td>"
        f"<td>{escape(item.to_status.value)}</td>"
        f"<td>{escape(item.changed_by)}</td>"
        f"<td>{escape(item.reason)}</td>"
        f"<td><pre>{escape(_to_pretty_json(item.metadata))}</pre></td>"
        "</tr>"
        for item in task_history
    ) or f"<tr><td colspan='6'>{escape(UI_TEXT['runtime_history_empty'])}</td></tr>"
    history_panel = ""
    if selected_task is not None:
        history_panel = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_history"])}: {escape(selected_task.task_id)}</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>changed_at</th><th>from</th><th>to</th><th>changed_by</th><th>reason</th><th>metadata_json</th></tr></thead>
          <tbody>{history_rows}</tbody>
        </table>
      </div>
    </section>
"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["runtime_tab"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell wide-shell">
    {_hero(UI_TEXT["runtime_panel_title"], UI_TEXT["dashboard_lede"])}
    {navigation("/runtime")}
    {_banner(message, message_level)}
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_panel_title"])}</h2>
      <form method="get" action="/runtime" class="grid two-col">
        <div class="field">
          <label for="runtime_db">{escape(UI_TEXT["runtime_db_path"])}</label>
          <input id="runtime_db" name="runtime_db" type="text" value="{escape(params["runtime_db"])}">
        </div>
        <div class="actions">
          <button class="secondary" type="submit">{escape(UI_TEXT["runtime_load_button"])}</button>
        </div>
      </form>
      <div class="actions">
        <span class="subtle">{escape(UI_TEXT["runtime_session_user"])}: {escape(session_user)}</span>
        <form method="post" action="/runtime/logout">
          <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
          <button class="secondary" type="submit">{escape(UI_TEXT["runtime_logout_button"])}</button>
        </form>
      </div>
    </section>
    {selected_review_html}
    {history_panel}
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_tasks"])}</h2>
      <div class="table-wrap"><table><thead><tr><th>task_id</th><th>trade_date</th><th>scope</th><th>action</th><th>status</th><th>SKU</th><th>platform</th></tr></thead><tbody>{task_rows}</tbody></table></div>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_reviews"])}</h2>
      <div class="table-wrap"><table><thead><tr><th>review_task_id</th><th>trade_date</th><th>scope</th><th>type</th><th>status</th><th>source_task_id</th><th>reason</th></tr></thead><tbody>{review_rows}</tbody></table></div>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_notifications"])}</h2>
      <div class="table-wrap"><table><thead><tr><th>notification_id</th><th>related_task_id</th><th>related_review_task_id</th><th>recipient</th><th>channel</th><th>send_status</th><th>sent_at</th><th>message</th></tr></thead><tbody>{notification_rows}</tbody></table></div>
    </section>
  </main>
</body>
</html>
"""


def render_runtime_page(
    *,
    params: dict[str, str],
    message: str,
    message_level: str,
    tasks,
    reviews,
    notifications,
    session_user: str | None,
    selected_review,
    selected_task,
    selected_notification,
    task_history,
) -> str:
    if session_user is None:
        login_panel = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_login_title"])}</h2>
      <form method="post" action="/runtime/login" class="grid two-col">
        <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
        <div class="field">
          <label for="runtime_username">{escape(UI_TEXT["runtime_login_user"])}</label>
          <input id="runtime_username" name="username" type="text" value="{escape(_runtime_admin_user())}">
        </div>
        <div class="field">
          <label for="runtime_password">{escape(UI_TEXT["runtime_login_password"])}</label>
          <input id="runtime_password" name="password" type="password" value="">
        </div>
        <div class="actions">
          <button class="primary" type="submit">{escape(UI_TEXT["runtime_login_button"])}</button>
        </div>
      </form>
    </section>
"""
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["runtime_tab"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell wide-shell">
    {_hero(UI_TEXT["runtime_panel_title"], UI_TEXT["dashboard_lede"])}
    {navigation("/runtime")}
    {_banner(message, message_level)}
    {login_panel}
  </main>
</body>
</html>
"""

    base_filters = {
        "task_trade_date": params.get("task_trade_date", ""),
        "task_status": params.get("task_status", ""),
        "review_trade_date": params.get("review_trade_date", ""),
        "review_status": params.get("review_status", ""),
        "notification_related_review_task_id": params.get("notification_related_review_task_id", ""),
        "notification_send_status": params.get("notification_send_status", ""),
    }
    task_status_options = "".join(
        f"<option value='{escape(option.value)}'{' selected' if params.get('task_status') == option.value else ''}>{escape(option.value)}</option>"
        for option in TaskStatus
    )
    review_status_options = "".join(
        f"<option value='{escape(option.value)}'{' selected' if params.get('review_status') == option.value else ''}>{escape(option.value)}</option>"
        for option in ReviewTaskStatus
    )
    notification_status_options = "".join(
        f"<option value='{escape(option.value)}'{' selected' if params.get('notification_send_status') == option.value else ''}>{escape(option.value)}</option>"
        for option in NotificationSendStatus
    )

    task_rows = "".join(
        "<tr>"
        f"<td><a href='/runtime?{escape(_build_runtime_query(params['runtime_db'], task_id=task.task_id, review_task_id=params.get('review_task_id', ''), notification_id=params.get('notification_id', ''), **base_filters))}'>{escape(task.task_id)}</a></td>"
        f"<td>{escape(task.trade_date.isoformat() if task.trade_date else '-')}</td>"
        f"<td>{escape(task.scope_type)}:{escape(task.scope_key)}</td>"
        f"<td>{escape(task.action_type.value)}</td>"
        f"<td>{escape(task.task_status.value)}</td>"
        f"<td>{escape(task.internal_sku or '-')}</td>"
        f"<td>{escape(task.platform_name or '-')}</td>"
        "</tr>"
        for task in tasks[:100]
    ) or "<tr><td colspan='7'>-</td></tr>"
    review_rows = "".join(
        "<tr>"
        f"<td><a href='/runtime?{escape(_build_runtime_query(params['runtime_db'], review_task_id=review.review_task_id, task_id=review.source_task_id or '', notification_id=params.get('notification_id', ''), **base_filters))}'>{escape(review.review_task_id)}</a></td>"
        f"<td>{escape(review.trade_date.isoformat() if review.trade_date else '-')}</td>"
        f"<td>{escape(review.scope_type)}:{escape(review.scope_key)}</td>"
        f"<td>{escape(review.review_type)}</td>"
        f"<td>{escape(review.review_status.value)}</td>"
        f"<td>{escape(review.source_task_id or '-')}</td>"
        f"<td>{escape(review.reason)}</td>"
        "</tr>"
        for review in reviews[:100]
    ) or "<tr><td colspan='7'>-</td></tr>"
    notification_rows = "".join(
        "<tr>"
        f"<td><a href='/runtime?{escape(_build_runtime_query(params['runtime_db'], notification_id=log.notification_id, review_task_id=params.get('review_task_id', ''), task_id=params.get('task_id', ''), **base_filters))}'>{escape(log.notification_id)}</a></td>"
        f"<td>{escape(log.related_task_id or '-')}</td>"
        f"<td>{escape(log.related_review_task_id or '-')}</td>"
        f"<td>{escape(log.recipient_type)}:{escape(log.recipient)}</td>"
        f"<td>{escape(log.channel)}</td>"
        f"<td>{escape(log.send_status)}</td>"
        f"<td>{escape(log.sent_at.isoformat() if log.sent_at else '-')}</td>"
        f"<td>{escape(log.message)}</td>"
        "</tr>"
        for log in notifications[:100]
    ) or f"<tr><td colspan='8'>{escape(UI_TEXT['runtime_notification_none'])}</td></tr>"

    selected_review_html = ""
    if selected_review is not None:
        next_source_status = (
            "approved->pending / rejected->skipped / adjusted->skipped / cancelled->cancelled"
            if selected_review.source_task_id
            else "-"
        )
        details = [
            ("review_task_id", selected_review.review_task_id),
            ("review_type", selected_review.review_type),
            ("review_status", selected_review.review_status.value),
            ("scope", f"{selected_review.scope_type}:{selected_review.scope_key}"),
            ("source_task_id", selected_review.source_task_id or "-"),
            ("required_by", selected_review.required_by.isoformat() if selected_review.required_by else "-"),
            ("reason", selected_review.reason or "-"),
            ("resolved_by", selected_review.resolved_by or "-"),
            ("resolved_at", selected_review.resolved_at.isoformat() if selected_review.resolved_at else "-"),
        ]
        detail_rows = "".join(f"<tr><th>{escape(label)}</th><td>{escape(str(value))}</td></tr>" for label, value in details)
        resolution_pre = escape(_to_pretty_json(selected_review.resolution_payload))
        review_payload_pre = escape(_to_pretty_json(selected_review.review_payload))
        related_notification_rows = "".join(
            "<tr>"
            f"<td><a href='/runtime?{escape(_build_runtime_query(params['runtime_db'], notification_id=log.notification_id, review_task_id=selected_review.review_task_id, task_id=selected_review.source_task_id or '', **base_filters))}'>{escape(log.notification_id)}</a></td>"
            f"<td>{escape(log.recipient_type)}:{escape(log.recipient)}</td>"
            f"<td>{escape(log.channel)}</td>"
            f"<td>{escape(log.send_status)}</td>"
            f"<td>{escape(log.sent_at.isoformat() if log.sent_at else '-')}</td>"
            f"<td>{escape(log.message)}</td>"
            "</tr>"
            for log in notifications
            if log.related_review_task_id == selected_review.review_task_id
        ) or "<tr><td colspan='6'>-</td></tr>"
        selected_review_html = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_review_detail"])}</h2>
      <div class="table-wrap"><table><tbody>{detail_rows}</tbody></table></div>
      <div class="grid two-col">
        <div class="field">
          <label>review_payload_json</label>
          <pre>{review_payload_pre}</pre>
        </div>
        <div class="field">
          <label>resolution_payload_json</label>
          <pre>{resolution_pre}</pre>
        </div>
      </div>
      <h3>{escape(UI_TEXT["runtime_notifications"])}</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>notification_id</th><th>recipient</th><th>channel</th><th>status</th><th>sent_at</th><th>message</th></tr></thead>
          <tbody>{related_notification_rows}</tbody>
        </table>
      </div>
    </section>
"""
        if selected_review.review_status == ReviewTaskStatus.PENDING:
            resolve_status_options = "".join(
                f"<option value='{escape(option.value)}'>{escape(option.value)}</option>"
                for option in (
                    ReviewTaskStatus.APPROVED,
                    ReviewTaskStatus.REJECTED,
                    ReviewTaskStatus.ADJUSTED,
                    ReviewTaskStatus.CANCELLED,
                )
            )
            hidden_filters = "".join(
                f"<input type='hidden' name='{escape(key)}' value='{escape(value)}'>"
                for key, value in base_filters.items()
            )
            selected_review_html += f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_review_handle"])}</h2>
      <form method="post" class="grid">
        <input type="hidden" name="action" value="resolve_review">
        <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
        <input type="hidden" name="review_task_id" value="{escape(selected_review.review_task_id)}">
        <input type="hidden" name="task_id" value="{escape(selected_review.source_task_id or '')}">
        <input type="hidden" name="notification_id" value="{escape(params.get("notification_id", ""))}">
        {hidden_filters}
        <div class="field">
          <label for="review_status">review_status</label>
          <select id="review_status" name="review_status">{resolve_status_options}</select>
        </div>
        <div class="field">
          <label for="reviewer_code">{escape(UI_TEXT["runtime_reviewer_code"])}</label>
          <input id="reviewer_code" name="reviewer_code" type="text" value="">
        </div>
        <div class="field">
          <label for="resolution_note">{escape(UI_TEXT["runtime_resolution_note"])}</label>
          <input id="resolution_note" name="resolution_note" type="text" value="">
        </div>
        <div class="field">
          <label for="resolution_payload_json">{escape(UI_TEXT["runtime_resolution_payload"])}</label>
          <textarea id="resolution_payload_json" name="resolution_payload_json" rows="8">{{}}</textarea>
        </div>
        <p class="subtle">source_task_status: {escape(next_source_status)}（仅当源任务当前处于 manual_review 时自动推动）</p>
        <div class="actions">
          <button class="primary" type="submit">{escape(UI_TEXT["runtime_submit_review"])}</button>
        </div>
      </form>
    </section>
"""

    history_panel = ""
    if selected_task is not None:
        history_rows = "".join(
            "<tr>"
            f"<td>{escape(item.changed_at.isoformat())}</td>"
            f"<td>{escape(item.from_status.value if item.from_status else '-')}</td>"
            f"<td>{escape(item.to_status.value)}</td>"
            f"<td>{escape(item.changed_by)}</td>"
            f"<td>{escape(item.reason)}</td>"
            f"<td>{_metadata_summary_rows(item.metadata)}</td>"
            "</tr>"
            for item in task_history
        ) or f"<tr><td colspan='6'>{escape(UI_TEXT['runtime_history_empty'])}</td></tr>"
        history_panel = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_history"])}: {escape(selected_task.task_id)}</h2>
      <p class="subtle">{escape(UI_TEXT["runtime_history_summary"])}</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>changed_at</th><th>from</th><th>to</th><th>changed_by</th><th>reason</th><th>summary</th></tr></thead>
          <tbody>{history_rows}</tbody>
        </table>
      </div>
    </section>
"""

    selected_notification_panel = ""
    if selected_notification is not None:
        notification_details = [
            ("notification_id", selected_notification.notification_id),
            ("related_task_id", selected_notification.related_task_id or "-"),
            ("related_review_task_id", selected_notification.related_review_task_id or "-"),
            ("recipient_type", selected_notification.recipient_type),
            ("recipient", selected_notification.recipient),
            ("channel", selected_notification.channel),
            ("send_status", selected_notification.send_status),
            ("sent_at", selected_notification.sent_at.isoformat() if selected_notification.sent_at else "-"),
            ("message", selected_notification.message or "-"),
            ("error_message", selected_notification.error_message or "-"),
        ]
        notification_detail_rows = "".join(
            f"<tr><th>{escape(label)}</th><td>{escape(str(value))}</td></tr>"
            for label, value in notification_details
        )
        selected_notification_panel = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_notification_detail"])}</h2>
      <div class="table-wrap"><table><tbody>{notification_detail_rows}</tbody></table></div>
    </section>
"""

    filter_panels = f"""
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_panel_title"])}</h2>
      <form method="get" action="/runtime" class="grid two-col">
        <div class="field">
          <label for="runtime_db">{escape(UI_TEXT["runtime_db_path"])}</label>
          <input id="runtime_db" name="runtime_db" type="text" value="{escape(params["runtime_db"])}">
        </div>
        <div class="actions">
          <button class="secondary" type="submit">{escape(UI_TEXT["runtime_load_button"])}</button>
        </div>
      </form>
      <div class="actions">
        <span class="subtle">{escape(UI_TEXT["runtime_session_user"])}: {escape(session_user)}</span>
        <form method="post" action="/runtime/logout">
          <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
          <button class="secondary" type="submit">{escape(UI_TEXT["runtime_logout_button"])}</button>
        </form>
      </div>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_task_filters"])}</h2>
      <form method="get" action="/runtime" class="grid two-col">
        <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
        <div class="field">
          <label for="task_trade_date">trade_date</label>
          <input id="task_trade_date" name="task_trade_date" type="text" value="{escape(params.get("task_trade_date", ""))}" placeholder="YYYY-MM-DD">
        </div>
        <div class="field">
          <label for="task_status">task_status</label>
          <select id="task_status" name="task_status"><option value="">-</option>{task_status_options}</select>
        </div>
        <div class="actions">
          <button class="secondary" type="submit">{escape(UI_TEXT["runtime_filter_apply"])}</button>
        </div>
      </form>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_review_filters"])}</h2>
      <form method="get" action="/runtime" class="grid two-col">
        <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
        <input type="hidden" name="task_trade_date" value="{escape(params.get("task_trade_date", ""))}">
        <input type="hidden" name="task_status" value="{escape(params.get("task_status", ""))}">
        <div class="field">
          <label for="review_trade_date">trade_date</label>
          <input id="review_trade_date" name="review_trade_date" type="text" value="{escape(params.get("review_trade_date", ""))}" placeholder="YYYY-MM-DD">
        </div>
        <div class="field">
          <label for="review_status">review_status</label>
          <select id="review_status" name="review_status"><option value="">-</option>{review_status_options}</select>
        </div>
        <div class="actions">
          <button class="secondary" type="submit">{escape(UI_TEXT["runtime_filter_apply"])}</button>
        </div>
      </form>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_notification_filters"])}</h2>
      <form method="get" action="/runtime" class="grid two-col">
        <input type="hidden" name="runtime_db" value="{escape(params["runtime_db"])}">
        <input type="hidden" name="task_trade_date" value="{escape(params.get("task_trade_date", ""))}">
        <input type="hidden" name="task_status" value="{escape(params.get("task_status", ""))}">
        <input type="hidden" name="review_trade_date" value="{escape(params.get("review_trade_date", ""))}">
        <input type="hidden" name="review_status" value="{escape(params.get("review_status", ""))}">
        <div class="field">
          <label for="notification_related_review_task_id">related_review_task_id</label>
          <input id="notification_related_review_task_id" name="notification_related_review_task_id" type="text" value="{escape(params.get("notification_related_review_task_id", ""))}">
        </div>
        <div class="field">
          <label for="notification_send_status">send_status</label>
          <select id="notification_send_status" name="notification_send_status"><option value="">-</option>{notification_status_options}</select>
        </div>
        <div class="actions">
          <button class="secondary" type="submit">{escape(UI_TEXT["runtime_filter_apply"])}</button>
        </div>
      </form>
    </section>
"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(UI_TEXT["runtime_tab"])}</title>
  {common_styles()}
</head>
<body>
  <main class="shell wide-shell">
    {_hero(UI_TEXT["runtime_panel_title"], UI_TEXT["dashboard_lede"])}
    {navigation("/runtime")}
    {_banner(message, message_level)}
    {filter_panels}
    {selected_review_html}
    {history_panel}
    {selected_notification_panel}
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_tasks"])}</h2>
      <div class="table-wrap"><table><thead><tr><th>task_id</th><th>trade_date</th><th>scope</th><th>action</th><th>status</th><th>SKU</th><th>platform</th></tr></thead><tbody>{task_rows}</tbody></table></div>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_reviews"])}</h2>
      <div class="table-wrap"><table><thead><tr><th>review_task_id</th><th>trade_date</th><th>scope</th><th>type</th><th>status</th><th>source_task_id</th><th>reason</th></tr></thead><tbody>{review_rows}</tbody></table></div>
    </section>
    <section class="panel">
      <h2>{escape(UI_TEXT["runtime_notifications"])}</h2>
      <div class="table-wrap"><table><thead><tr><th>notification_id</th><th>related_task_id</th><th>related_review_task_id</th><th>recipient</th><th>channel</th><th>send_status</th><th>sent_at</th><th>message</th></tr></thead><tbody>{notification_rows}</tbody></table></div>
    </section>
  </main>
</body>
</html>
"""


def navigation(active_path: str) -> str:
    dashboard_class = "nav-link active" if active_path == "/" else "nav-link"
    tables_class = "nav-link active" if active_path == "/tables" else "nav-link"
    execution_class = "nav-link active" if active_path == "/execution" else "nav-link"
    manual_class = "nav-link active" if active_path == "/manual-intervention" else "nav-link"
    runtime_class = "nav-link active" if active_path == "/runtime" else "nav-link"
    return (
        "<nav class='nav-strip'>"
        f"<a class='{dashboard_class}' href='/'>{escape(UI_TEXT['dashboard_tab'])}</a>"
        f"<a class='{tables_class}' href='/tables'>{escape(UI_TEXT['tables_tab'])}</a>"
        f"<a class='{execution_class}' href='/execution'>{escape(UI_TEXT['execution_tab'])}</a>"
        f"<a class='{manual_class}' href='/manual-intervention'>{escape(UI_TEXT['manual_tab'])}</a>"
        f"<a class='{runtime_class}' href='/runtime'>{escape(UI_TEXT['runtime_tab'])}</a>"
        "</nav>"
    )


def common_styles() -> str:
    return """
  <style>
    :root {
      --bg: #f2ecdf;
      --panel: rgba(255,255,255,0.92);
      --ink: #1f2a30;
      --muted: #5f6d73;
      --accent: #b05833;
      --accent-soft: #ecd7cb;
      --success: #285844;
      --success-bg: #dceddf;
      --error: #8a2f2f;
      --error-bg: #f6dddd;
      --info-bg: #ece6da;
      --line: rgba(31,42,48,0.12);
      --shadow: 0 20px 60px rgba(91, 67, 49, 0.13);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      font-family: Georgia, "Times New Roman", "Noto Serif SC", serif;
      background:
        radial-gradient(circle at top left, rgba(176,88,51,0.14), transparent 28%),
        radial-gradient(circle at 88% 10%, rgba(40,88,68,0.12), transparent 22%),
        linear-gradient(180deg, #faf5eb 0%, var(--bg) 100%);
      min-height: 100vh;
    }
    .shell {
      width: min(1120px, calc(100% - 32px));
      margin: 28px auto 44px;
    }
    .wide-shell {
      width: min(1380px, calc(100% - 24px));
    }
    .hero {
      padding: 28px 30px 24px;
      border: 1px solid var(--line);
      border-radius: 28px;
      background: linear-gradient(135deg, rgba(255,255,255,0.86), rgba(255,248,243,0.94));
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
    }
    .hero::after {
      content: "";
      position: absolute;
      inset: auto -90px -90px auto;
      width: 260px;
      height: 260px;
      background: radial-gradient(circle, rgba(176,88,51,0.12), transparent 72%);
    }
    h1 {
      margin: 0 0 10px;
      font-size: clamp(34px, 5vw, 58px);
      line-height: 0.94;
      letter-spacing: -0.03em;
    }
    h2 {
      margin: 0 0 16px;
      font-size: 24px;
    }
    .lede {
      max-width: 820px;
      margin: 0;
      color: var(--muted);
      font-size: 17px;
      line-height: 1.55;
    }
    .nav-strip {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin: 18px 0;
    }
    .nav-link {
      text-decoration: none;
      color: var(--ink);
      padding: 12px 16px;
      border-radius: 999px;
      background: rgba(255,255,255,0.7);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
    }
    .nav-link.active {
      background: var(--accent);
      color: white;
      border-color: transparent;
    }
    .layout {
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 18px;
      margin-top: 18px;
    }
    .panel {
      padding: 22px;
      border: 1px solid var(--line);
      border-radius: 24px;
      background: var(--panel);
      box-shadow: var(--shadow);
      backdrop-filter: blur(6px);
      margin-top: 18px;
    }
    .grid {
      display: grid;
      gap: 14px;
    }
    .two-col {
      grid-template-columns: 1fr 1.6fr;
      align-items: end;
    }
    .field {
      display: grid;
      gap: 8px;
    }
    .field label, .checkbox {
      font-size: 13px;
      color: var(--muted);
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    input[type="text"], input[type="password"], select, textarea {
      width: 100%;
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 16px;
      font: inherit;
      color: var(--ink);
      background: rgba(255,255,255,0.95);
    }
    textarea {
      resize: vertical;
      min-height: 120px;
    }
    .checkbox {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .actions {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 8px;
    }
    button {
      border: 0;
      border-radius: 999px;
      padding: 13px 18px;
      font: inherit;
      cursor: pointer;
      transition: transform 140ms ease, opacity 140ms ease;
    }
    button:hover { transform: translateY(-1px); }
    .primary {
      background: var(--accent);
      color: white;
    }
    .secondary {
      background: var(--accent-soft);
      color: var(--ink);
    }
    .sticky-actions {
      position: sticky;
      bottom: 10px;
      padding-top: 16px;
      background: linear-gradient(180deg, rgba(255,255,255,0), rgba(255,255,255,0.92) 35%);
    }
    .confirm-box {
      margin-top: 16px;
      padding: 16px;
      border-radius: 18px;
      background: rgba(236, 215, 203, 0.5);
      border: 1px solid rgba(176,88,51,0.18);
    }
    .banner {
      margin-top: 18px;
      padding: 14px 16px;
      border-radius: 16px;
      font-size: 15px;
      border: 1px solid transparent;
    }
    .banner.success {
      background: var(--success-bg);
      color: var(--success);
    }
    .banner.error {
      background: var(--error-bg);
      color: var(--error);
    }
    .banner.info {
      background: var(--info-bg);
      color: var(--ink);
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 12px;
    }
    .metric {
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(255,248,243,0.86);
      border: 1px solid rgba(176,88,51,0.12);
    }
    .metric .label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .metric strong {
      display: block;
      margin-top: 6px;
      font-size: 28px;
      line-height: 1;
    }
    .subtle {
      color: var(--muted);
      margin: 8px 0 0;
      font-size: 14px;
      word-break: break-all;
    }
    .table-wrap {
      overflow-x: auto;
      margin-top: 16px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 640px;
    }
    th, td {
      padding: 12px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      font-size: 14px;
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.04em;
      white-space: nowrap;
    }
    .editor-table {
      min-width: 980px;
    }
    .editor-table th:first-child,
    .editor-table td:first-child {
      position: sticky;
      left: 0;
      background: #f8f1e7;
      z-index: 1;
    }
    .row-index {
      color: var(--muted);
      width: 44px;
      white-space: nowrap;
    }
    .cell-input {
      min-width: 140px;
      padding: 10px 12px;
      border-radius: 12px;
    }
    .cell-input.invalid {
      border-color: rgba(138,47,47,0.5);
      background: rgba(246,221,221,0.5);
    }
    .cell-issue {
      margin-top: 6px;
      color: var(--error);
      font-size: 12px;
      line-height: 1.35;
    }
    .issue-list {
      margin: 16px 0 0;
      padding-left: 20px;
      color: var(--error);
      line-height: 1.5;
    }
    .aside-list {
      display: grid;
      gap: 12px;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.5;
    }
    .aside-list code {
      display: block;
      margin-top: 4px;
      padding: 10px 12px;
      border-radius: 12px;
      background: rgba(29,42,49,0.05);
      color: var(--ink);
      font-family: "Cascadia Mono", Consolas, monospace;
      font-size: 13px;
      word-break: break-all;
    }
    pre {
      margin: 0;
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(29,42,49,0.05);
      color: var(--ink);
      font-family: "Cascadia Mono", Consolas, monospace;
      font-size: 12px;
      white-space: pre-wrap;
      word-break: break-word;
    }
    @media (max-width: 960px) {
      .layout, .two-col { grid-template-columns: 1fr; }
      .shell, .wide-shell {
        width: min(100% - 18px, 1380px);
        margin-top: 18px;
      }
      .hero, .panel {
        padding: 18px;
        border-radius: 20px;
      }
      .nav-link {
        flex: 1 1 auto;
        text-align: center;
      }
    }
  </style>
"""


def _hero(title: str, description: str) -> str:
    return (
        "<section class='hero'>"
        f"<h1>{escape(title)}</h1>"
        f"<p class='lede'>{escape(description)}</p>"
        "</section>"
    )


def _banner(message: str, level: str) -> str:
    if not message:
        return ""
    return f"<div class='banner {escape(level)}'>{escape(message)}</div>"


def _parse_query(environ) -> dict[str, list[str]]:
    return parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)


def _parse_body(environ) -> dict[str, list[str]]:
    size = int(environ.get("CONTENT_LENGTH") or 0)
    body = environ["wsgi.input"].read(size).decode("utf-8")
    return parse_qs(body)


def _runtime_admin_user() -> str:
    return os.getenv("RUNTIME_ADMIN_USER", "admin")


def _dev_mode() -> bool:
    return os.getenv("DEV_MODE", "").strip().lower() == "true"


def _session_cookie_headers(session_user: str) -> list[tuple[str, str]]:
    _cleanup_runtime_sessions()
    session_id = token_urlsafe(24)
    expires_at = datetime.now() + timedelta(seconds=RUNTIME_SESSION_TTL_SECONDS)
    _RUNTIME_SESSIONS[session_id] = {"user": session_user, "expires_at": expires_at}
    cookie = SimpleCookie()
    cookie[RUNTIME_SESSION_COOKIE] = session_id
    cookie[RUNTIME_SESSION_COOKIE]["path"] = "/"
    cookie[RUNTIME_SESSION_COOKIE]["httponly"] = True
    cookie[RUNTIME_SESSION_COOKIE]["samesite"] = "Lax"
    cookie[RUNTIME_SESSION_COOKIE]["max-age"] = str(RUNTIME_SESSION_TTL_SECONDS)
    return [("Set-Cookie", cookie.output(header="").strip())]


def _clear_session_cookie_headers() -> list[tuple[str, str]]:
    cookie = SimpleCookie()
    cookie[RUNTIME_SESSION_COOKIE] = ""
    cookie[RUNTIME_SESSION_COOKIE]["path"] = "/"
    cookie[RUNTIME_SESSION_COOKIE]["httponly"] = True
    cookie[RUNTIME_SESSION_COOKIE]["samesite"] = "Lax"
    cookie[RUNTIME_SESSION_COOKIE]["max-age"] = "0"
    cookie[RUNTIME_SESSION_COOKIE]["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    return [("Set-Cookie", cookie.output(header="").strip())]


def _get_runtime_session_user(environ) -> str | None:
    _cleanup_runtime_sessions()
    cookie_header = environ.get("HTTP_COOKIE", "")
    if not cookie_header:
        return None
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    session_cookie = cookie.get(RUNTIME_SESSION_COOKIE)
    if session_cookie is None:
        return None
    session = _RUNTIME_SESSIONS.get(session_cookie.value)
    if session is None:
        return None
    expires_at = session.get("expires_at")
    if not isinstance(expires_at, datetime) or expires_at <= datetime.now():
        _RUNTIME_SESSIONS.pop(session_cookie.value, None)
        return None
    user = session.get("user")
    return str(user) if user else None


def _cleanup_runtime_sessions() -> None:
    now = datetime.now()
    expired = [
        session_id
        for session_id, payload in _RUNTIME_SESSIONS.items()
        if not isinstance(payload.get("expires_at"), datetime) or payload.get("expires_at") <= now
    ]
    for session_id in expired:
        _RUNTIME_SESSIONS.pop(session_id, None)


def _map_review_to_source_task_status(status: ReviewTaskStatus) -> TaskStatus:
    mapping = {
        ReviewTaskStatus.APPROVED: TaskStatus.PENDING,
        ReviewTaskStatus.REJECTED: TaskStatus.SKIPPED,
        ReviewTaskStatus.ADJUSTED: TaskStatus.SKIPPED,
        ReviewTaskStatus.CANCELLED: TaskStatus.CANCELLED,
    }
    return mapping[status]


def _parse_resolution_payload(payload_text: str) -> dict[str, object]:
    raw = payload_text.strip() or "{}"
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"resolution_payload_json 不是合法 JSON: {exc.msg}") from exc
    if not isinstance(loaded, dict):
        raise ValidationError("resolution_payload_json 必须是 JSON 对象。")
    return loaded


def _build_runtime_query(
    runtime_db: str,
    *,
    review_task_id: str = "",
    task_id: str = "",
    notification_id: str = "",
    task_trade_date: str = "",
    task_status: str = "",
    review_trade_date: str = "",
    review_status: str = "",
    notification_related_review_task_id: str = "",
    notification_send_status: str = "",
    message: str = "",
    level: str = "",
) -> str:
    params = {"runtime_db": runtime_db}
    if review_task_id:
        params["review_task_id"] = review_task_id
    if task_id:
        params["task_id"] = task_id
    if notification_id:
        params["notification_id"] = notification_id
    if task_trade_date:
        params["task_trade_date"] = task_trade_date
    if task_status:
        params["task_status"] = task_status
    if review_trade_date:
        params["review_trade_date"] = review_trade_date
    if review_status:
        params["review_status"] = review_status
    if notification_related_review_task_id:
        params["notification_related_review_task_id"] = notification_related_review_task_id
    if notification_send_status:
        params["notification_send_status"] = notification_send_status
    if message:
        params["message"] = message
    if level:
        params["level"] = level
    return urlencode(params)


def _build_runtime_url(
    runtime_db: str,
    *,
    review_task_id: str = "",
    task_id: str = "",
    notification_id: str = "",
    task_trade_date: str = "",
    task_status: str = "",
    review_trade_date: str = "",
    review_status: str = "",
    notification_related_review_task_id: str = "",
    notification_send_status: str = "",
    message: str = "",
    level: str = "",
) -> str:
    return (
        "/runtime?"
        + _build_runtime_query(
            runtime_db,
            review_task_id=review_task_id,
            task_id=task_id,
            notification_id=notification_id,
            task_trade_date=task_trade_date,
            task_status=task_status,
            review_trade_date=review_trade_date,
            review_status=review_status,
            notification_related_review_task_id=notification_related_review_task_id,
            notification_send_status=notification_send_status,
            message=message,
            level=level,
        )
    )


def _redirect_response(location: str, headers: list[tuple[str, str]] | None = None) -> tuple[str, str, list[tuple[str, str]]]:
    response_headers = [("Location", location)]
    if headers:
        response_headers.extend(headers)
    return ("303 See Other", "", response_headers)


def _to_pretty_json(value: object) -> str:
    if not value:
        return "{}"
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _metadata_summary_rows(metadata: object) -> str:
    if not isinstance(metadata, dict) or not metadata:
        return "-"
    parts: list[str] = []
    for key in sorted(metadata.keys()):
        value = metadata[key]
        if isinstance(value, dict):
            value_text = ", ".join(f"{sub_key}={value[sub_key]}" for sub_key in sorted(value.keys()))
        else:
            value_text = str(value)
        parts.append(f"{key}: {value_text}")
    return "<br>".join(escape(part) for part in parts)


def _runtime_filter_state(params: dict[str, str]) -> dict[str, str]:
    return {
        "task_trade_date": params.get("task_trade_date", ""),
        "task_status": params.get("task_status", ""),
        "review_trade_date": params.get("review_trade_date", ""),
        "review_status": params.get("review_status", ""),
        "notification_related_review_task_id": params.get("notification_related_review_task_id", ""),
        "notification_send_status": params.get("notification_send_status", ""),
    }


def _extract_table_rows(parsed: dict[str, list[str]], headers: list[str]) -> list[dict[str, object]]:
    row_indexes: set[int] = set()
    for key in parsed:
        if not key.startswith("cell__"):
            continue
        parts = key.split("__", 2)
        if len(parts) != 3:
            continue
        try:
            row_indexes.add(int(parts[1]))
        except ValueError:
            continue

    rows: list[dict[str, object]] = []
    for row_index in sorted(row_indexes):
        row = {header: _first(parsed, f"cell__{row_index}__{header}", "") for header in headers}
        if any(str(value).strip() for value in row.values()):
            rows.append(row)
    return rows


def _respond(start_response, status: str, content_type: str, body: str, *, headers: list[tuple[str, str]] | None = None):
    payload = body.encode("utf-8")
    response_headers = [("Content-Type", content_type), ("Content-Length", str(len(payload)))]
    if headers:
        response_headers.extend(headers)
    start_response(status, response_headers)
    return [payload]


def _first(values: dict[str, list[str]], key: str, default: str) -> str:
    return values.get(key, [default])[0]

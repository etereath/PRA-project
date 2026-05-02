from __future__ import annotations

import os
from html import escape
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from app.exceptions import TableValidationError, ValidationError
from app.repositories.workbook_repository import (
    get_table_headers,
    load_table_records,
    save_table_records,
)
from app.services.workflow import (
    ExecutionSimulationInputs,
    ExecutionSimulationSummary,
    TaskGenerationSummary,
    ValidationSummary,
    WorkflowInputs,
    generate_tasks_from_sources,
    preview_tasks_from_sources,
    simulate_execution_from_tasks,
    validate_sources,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRODUCTS = ROOT / "data" / "samples" / "products.xlsx"
DEFAULT_PRICE_RULES = ROOT / "data" / "samples" / "price_rules.xlsx"
DEFAULT_LISTING_RULES = ROOT / "data" / "samples" / "listing_rules.xlsx"
DEFAULT_OUTPUT = ROOT / "data" / "samples" / "web_generated_tasks.xlsx"
DEFAULT_EXECUTION_LOGS = ROOT / "data" / "samples" / "web_execution_logs.xlsx"
DEFAULT_EXECUTED_TASKS = ROOT / "data" / "samples" / "web_executed_tasks.xlsx"

TABLE_OPTIONS = {
    "products": {"label": "\u5546\u54c1\u4e3b\u8868", "path": DEFAULT_PRODUCTS},
    "price_rules": {"label": "\u4ef7\u683c\u89c4\u5219\u8868", "path": DEFAULT_PRICE_RULES},
    "listing_rules": {"label": "\u4e0a\u4e0b\u67b6\u89c4\u5219\u8868", "path": DEFAULT_LISTING_RULES},
}

TABLE_HEADER_LABELS = {
    "internal_sku": "\u5185\u90e8 SKU",
    "product_name": "\u5546\u54c1\u540d\u79f0",
    "variety": "\u54c1\u79cd",
    "grade": "\u7b49\u7ea7",
    "stem_length": "\u679d\u957f/\u89c4\u683c",
    "unit": "\u5355\u4f4d",
    "base_cost": "\u57fa\u7840\u6210\u672c",
    "current_stock": "\u5f53\u524d\u5e93\u5b58",
    "sale_enabled": "\u5141\u8bb8\u9500\u552e",
    "last_price": "\u4e0a\u6b21\u552e\u4ef7",
    "remark": "\u5907\u6ce8",
    "feature_season": "\u5b63\u8282\u7279\u5f81",
    "feature_color": "\u989c\u8272\u7279\u5f81",
    "rule_id": "\u89c4\u5219 ID",
    "rule_name": "\u89c4\u5219\u540d\u79f0",
    "scope_type": "\u4f5c\u7528\u8303\u56f4\u7c7b\u578b",
    "scope_value": "\u4f5c\u7528\u8303\u56f4\u503c",
    "pricing_method": "\u5b9a\u4ef7\u65b9\u5f0f",
    "markup_value": "\u52a0\u4ef7\u503c",
    "min_price": "\u6700\u4f4e\u4ef7",
    "rounding_rule": "\u53d6\u6574\u89c4\u5219",
    "rounding_step": "\u53d6\u6574\u6b65\u957f",
    "active": "\u662f\u5426\u542f\u7528",
    "priority": "\u4f18\u5148\u7ea7",
    "condition_type": "\u6761\u4ef6\u7c7b\u578b",
    "condition_value": "\u6761\u4ef6\u503c",
    "action": "\u6267\u884c\u52a8\u4f5c",
}

UI_TEXT = {
    "site_title": "PRA \u7ba1\u7406\u53f0",
    "dashboard_title": "PRA \u7ba1\u7406\u53f0",
    "dashboard_lede": "\u5f53\u524d\u9875\u9762\u7528\u4e8e\u6821\u9a8c Excel \u6570\u636e\u3001\u751f\u6210\u4efb\u52a1\uff0c\u5e76\u5728\u4fdd\u7559 AI \u5b9a\u4ef7\u5efa\u8bae\u5f00\u5173\u7684\u524d\u63d0\u4e0b\u5feb\u901f\u9a8c\u8bc1\u6574\u6761\u4e1a\u52a1\u94fe\u8def\u3002",
    "dashboard_tab": "\u4efb\u52a1\u9762\u677f",
    "tables_tab": "Excel \u8868\u683c\u7ba1\u7406",
    "execution_tab": "\u6267\u884c\u56de\u5199",
    "task_panel_title": "\u4efb\u52a1\u751f\u6210",
    "execution_panel_title": "\u6a21\u62df\u6267\u884c\u4e0e\u56de\u5199",
    "resources_title": "\u5185\u7f6e\u8d44\u6e90",
    "products_path": "\u5546\u54c1\u8868\u8def\u5f84",
    "price_rules_path": "\u4ef7\u683c\u89c4\u5219\u8def\u5f84",
    "listing_rules_path": "\u4e0a\u4e0b\u67b6\u89c4\u5219\u8def\u5f84",
    "output_path": "\u4efb\u52a1\u8f93\u51fa\u8def\u5f84",
    "platform_name": "\u5e73\u53f0\u540d\u79f0",
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


def navigation(active_path: str) -> str:
    dashboard_class = "nav-link active" if active_path == "/" else "nav-link"
    tables_class = "nav-link active" if active_path == "/tables" else "nav-link"
    execution_class = "nav-link active" if active_path == "/execution" else "nav-link"
    return (
        "<nav class='nav-strip'>"
        f"<a class='{dashboard_class}' href='/'>{escape(UI_TEXT['dashboard_tab'])}</a>"
        f"<a class='{tables_class}' href='/tables'>{escape(UI_TEXT['tables_tab'])}</a>"
        f"<a class='{execution_class}' href='/execution'>{escape(UI_TEXT['execution_tab'])}</a>"
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
    input[type="text"], select {
      width: 100%;
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 16px;
      font: inherit;
      color: var(--ink);
      background: rgba(255,255,255,0.95);
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


def _parse_body(environ) -> dict[str, list[str]]:
    size = int(environ.get("CONTENT_LENGTH") or 0)
    body = environ["wsgi.input"].read(size).decode("utf-8")
    return parse_qs(body)


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


def _respond(start_response, status: str, content_type: str, body: str):
    payload = body.encode("utf-8")
    start_response(status, [("Content-Type", content_type), ("Content-Length", str(len(payload)))])
    return [payload]


def _first(values: dict[str, list[str]], key: str, default: str) -> str:
    return values.get(key, [default])[0]

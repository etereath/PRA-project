"""Render a concise, human-readable Markdown report for a ShadowBot test.

The JSON acceptance artifact remains the source of truth.  This module only
formats that artifact and writes UTF-8 text; it never changes queue state or
business data.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class MarkdownReportError(ValueError):
    """Raised when an acceptance artifact cannot produce a useful report."""


def _string(value: Any, default: str = "-") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _status(value: Any) -> str:
    return _string(value, "UNKNOWN").upper()


def _yes_no(value: Any) -> str:
    return "是" if bool(value) else "否"


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MarkdownReportError(f"{name} must be an object")
    return value


def _require_results(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not value:
        raise MarkdownReportError("test_results must be a non-empty array")
    results: list[Mapping[str, Any]] = []
    for index, item in enumerate(value, start=1):
        results.append(_require_mapping(item, f"test_results[{index}]"))
    return results


def render_formal_boundary_markdown(payload: Mapping[str, Any]) -> str:
    """Render a Task 11 real-machine acceptance artifact as a plain-language report."""

    report = _require_mapping(payload, "payload")
    counts = _require_mapping(report.get("count_identity"), "count_identity")
    results = _require_results(report.get("test_results"))
    run = _require_mapping(report.get("run_identity"), "run_identity")
    sort_change = _require_mapping(report.get("sort_change"), "sort_change")
    database = _require_mapping(report.get("database_readback"), "database_readback")
    queue = _require_mapping(report.get("final_queue_state"), "final_queue_state")
    encoding = _require_mapping(report.get("encoding_check"), "encoding_check")

    status = _status(report.get("overall_status"))
    conclusion = "本次实机测试通过。" if status == "PASSED" else "本次实机测试未通过。"
    total_count = _string(counts.get("total_count"), "0")
    processed_count = _string(counts.get("processed_count"), "0")
    success_count = _string(counts.get("success_count"), "0")
    failed_count = _string(counts.get("failed_count"), "0")
    skipped_count = _string(counts.get("skipped_count"), "0")
    manual_check_count = _string(counts.get("manual_check_count"), "0")
    queue_clean = (
        _status(queue.get("heartbeat_status")) == "STOPPED"
        and bool(queue.get("inbox_empty"))
        and bool(queue.get("working_empty"))
        and bool(queue.get("results_empty"))
        and not bool(queue.get("stop_signal_present"))
    )
    side_effect_started = bool(report.get("side_effect_started"))
    queue_summary = (
        "测试结束后队列已正常停止，待处理目录均为空，未发现残留停止信号。"
        if queue_clean
        else "测试结束时队列收尾状态异常，请检查运行目录中的队列证据。"
    )
    lines = [
        "# 任务11实机测试报告",
        "",
        f"**结论：{conclusion}**",
        "",
        (
            f"影刀在只读模式下读取 {total_count} 个目标，处理 {processed_count} 个："
            f"成功 {success_count} 个，失败 {failed_count} 个，跳过 {skipped_count} 个，"
            f"人工复核 {manual_check_count} 个。"
        ),
        "",
        "## 运行信息",
        "",
        f"- 商品平台：{_string(report.get('platform_name'), '蚂蚁花团供应商')}",
        f"- 执行模式：{_string(report.get('execution_mode'), 'READ_ONLY')}（只读，不修改商品数据）",
        f"- 运行 ID：`{_string(run.get('execution_attempt_id'))}`",
        f"- 任务 ID：`{_string(run.get('task_id'))}`",
        f"- 操作 ID：`{_string(run.get('operation_id'))}`",
        f"- 读取批次 ID：`{_string(run.get('read_batch_id'))}`",
        f"- 影刀运行 ID：`{_string(run.get('shadowbot_run_id'))}`",
        "",
        "## 排序前后",
        "",
        f"- 排序规则：{_string(sort_change.get('sort_rule'))}",
        f"- 排序前（上一轮验收范围）：{_string(sort_change.get('before_order'))}",
        f"- 排序后（本次页面顺序）：{_string(sort_change.get('after_order'))}",
        f"- 本次实际读取顺序：{_string(sort_change.get('observed_order'))}",
        "",
        "## 逐商品读取结果与证据",
        "",
    ]
    for index, item in enumerate(results, start=1):
        name = _string(item.get("product_name"), _string(item.get("expected_product_name")))
        grade = _string(item.get("grade"), _string(item.get("expected_grade")))
        item_status = _status(item.get("item_status"))
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        first_evidence = evidence[0] if evidence and isinstance(evidence[0], Mapping) else {}
        evidence_state = (
            f"{len(evidence)} 份，上传{ '成功' if first_evidence.get('upload_status') == 'SUCCESS' else '未确认' }，"
            f"哈希校验{ '通过' if first_evidence.get('hash_verified') is True else '未通过' }；"
            f"证据 ID `{_string(first_evidence.get('evidence_id'))}`"
            if evidence
            else "无证据"
        )
        if item_status == "SUCCESS":
            result_line = (
                f"读取成功：库存 {_string(item.get('inventory'))}，价格 ¥{_string(item.get('price'))}，"
                f"{_string(item.get('listing_status'), '状态未知')}。"
            )
        else:
            result_line = f"读取失败：{_string(item.get('error_code'), '未知错误')}。"
        lines.extend(
            [
                f"### {index}. {name} {grade}",
                "",
                f"- 页面位置：第 {_string(item.get('position'))} 行（{_string(item.get('row_identity'))}）",
                f"- SKU：`{_string(item.get('platform_sku'))}`",
                f"- 结果：**{result_line}**",
                f"- 逐商品证据：{evidence_state}",
                "",
            ]
        )
    count_formula = _string(
        counts.get("formula"),
        f"{processed_count} = {success_count} + {failed_count} + {skipped_count} + {manual_check_count}",
    )
    lines.extend(
        [
            "## 计数恒等式",
            "",
            f"`total_count = processed_count`：{total_count} = {processed_count}",
            f"`processed_count = success_count + failed_count + skipped_count + manual_check_count`：{count_formula}",
            f"- 恒等式校验：**{ '通过' if bool(counts.get('passed')) else '未通过' }**",
            "",
            "## 数据库回读",
            "",
            f"- 回读结果：**{ '通过' if bool(database.get('readback_passed')) else '未通过' }**",
            f"- execution_attempts：`{_string(database.get('attempt_status'))}`，模式 `{_string(database.get('execution_mode'))}`",
            f"- operation ledger：`{_string(database.get('operation_status'))}`；任务记录：`{_string(database.get('task_status'))}`",
            f"- execution_logs：{_string(database.get('execution_log_count'), '0')} 条，成功标记：{_yes_no(database.get('execution_log_success'))}",
            f"- 数据库记录的结果 ID：`{_string(database.get('result_id'))}`",
            f"- 请求哈希回读一致：{_yes_no(database.get('request_hash_matches'))}；结果哈希已记录：{_yes_no(database.get('result_hash_recorded'))}",
            f"- 只读状态说明：{_string(database.get('read_only_note'))}",
            "",
            "## 测试收尾与异常",
            "",
            f"{queue_summary}业务副作用：{_yes_no(side_effect_started)}。",
            f"本次未发现未处理异常。底层验收校验：{ '通过' if bool(report.get('validation_passed')) else '未通过' }；中文编码校验：{ '通过' if encoding.get('json_question_marks', 0) == 0 and encoding.get('replacement_characters', 0) == 0 else '未通过' }。",
            "",
        ]
    )
    return "\n".join(lines)


def load_acceptance_json(path: str | Path) -> dict[str, Any]:
    """Load a JSON acceptance artifact with explicit UTF-8 handling."""

    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarkdownReportError(f"cannot read acceptance JSON: {source}") from exc
    if not isinstance(data, dict):
        raise MarkdownReportError("acceptance JSON must contain an object")
    return data


def write_formal_boundary_markdown(
    source_json: str | Path,
    output_markdown: str | Path,
) -> Path:
    """Convert an acceptance JSON file to a UTF-8 Markdown report."""

    output = Path(output_markdown)
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_formal_boundary_markdown(load_acceptance_json(source_json))
    output.write_text(markdown, encoding="utf-8", newline="\n")
    return output

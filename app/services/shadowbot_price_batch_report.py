"""Task 12 machine-readable acceptance and human-readable Markdown reports."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.exceptions import ValidationError
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_price_batch import (
    BatchItemStatus,
    aggregate_batch_counts,
)
from app.utils import serialize_decimal


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def build_price_batch_acceptance(
    repository: SQLiteRuntimeRepository,
    batch_id: str,
    *,
    queue_dir: Path | None = None,
) -> dict[str, Any]:
    batch = repository.get_shadowbot_batch(batch_id)
    if batch is None:
        raise ValidationError(f"price batch not found: {batch_id}")
    items = repository.list_shadowbot_batch_items(batch_id)
    item_reports: list[dict[str, Any]] = []
    all_run_ids: list[str] = []
    database_errors: list[str] = []
    for item in items:
        operation = repository.get_shadowbot_operation(item.operation_id)
        task = repository.get_task(item.task_id)
        review = repository.get_review_task(item.review_task_id)
        attempts = repository.list_shadowbot_execution_attempts(operation_id=item.operation_id)
        attempt_reports = []
        for attempt in attempts:
            if attempt.shadowbot_run_id and attempt.shadowbot_run_id not in all_run_ids:
                all_run_ids.append(attempt.shadowbot_run_id)
            attempt_reports.append(
                {
                    "execution_attempt_id": attempt.execution_attempt_id,
                    "execution_mode": attempt.execution_mode,
                    "shadowbot_run_id": attempt.shadowbot_run_id,
                    "status": attempt.status,
                    "side_effect_state": attempt.side_effect_state,
                    "instruction_hash": attempt.instruction_hash,
                    "request_file_sha256": _prefixed_hash(attempt.request_file_sha256),
                    "result_id": str(attempt.raw_output.get("result_id") or ""),
                    "result_file_sha256": _prefixed_hash(
                        attempt.raw_output.get("result_file_sha256")
                    ),
                    "started_at": _iso(attempt.started_at),
                    "ended_at": _iso(attempt.ended_at),
                    "archive": _archive_state(queue_dir, attempt.execution_attempt_id),
                }
            )
        if operation is None or operation.task_id != item.task_id:
            database_errors.append(f"{item.item_id}: operation/task binding mismatch")
        if task is None:
            database_errors.append(f"{item.item_id}: task missing")
        if review is None or review.source_task_id != item.task_id:
            database_errors.append(f"{item.item_id}: review/task binding mismatch")
        item_reports.append(
            {
                "batch_id": batch.batch_id,
                "item_id": item.item_id,
                "ordinal": item.ordinal,
                "product_name": item.expected_product_name,
                "grade": item.expected_grade,
                "platform_sku": item.external_platform_sku,
                "approved_old_price": serialize_decimal(item.approved_expected_old_price),
                "fresh_old_price": (
                    serialize_decimal(item.fresh_old_price) if item.fresh_old_price is not None else None
                ),
                "target_price": serialize_decimal(item.target_price),
                "post_price": (
                    serialize_decimal(item.post_commit_price)
                    if item.post_commit_price is not None
                    else None
                ),
                "status": item.status,
                "error_code": item.error_code,
                "error_message": item.error_message,
                "task_id": item.task_id,
                "review_task_id": item.review_task_id,
                "operation_id": item.operation_id,
                "fresh_read_attempt_id": item.fresh_read_attempt_id,
                "write_attempt_id": item.current_execution_attempt_id,
                "reconcile_attempt_id": item.reconcile_attempt_id,
                "reconciliation_outcome": item.reconciliation_outcome,
                "result_id": item.result_id,
                "result_hash": item.result_hash,
                "approved_payload_hash": item.approved_payload_hash,
                "page_identity_key": item.page_identity_key,
                "write_identity_key": item.write_identity_key,
                "source_read_batch_id": item.source_read_batch_id,
                "source_snapshot_sha256": item.source_snapshot_sha256,
                "source_page_context_sha256": item.source_page_context_sha256,
                "attempts": attempt_reports,
            }
        )
    counts = aggregate_batch_counts([item.status for item in items])
    recorded_counts = {
        name: (len(items) if name == "total_count" else getattr(batch, name))
        for name in counts
    }
    payload: dict[str, Any] = {
        "report_type": "TASK12_PRICE_BATCH_ACCEPTANCE",
        "contract_version": 3,
        "batch": {
            "batch_id": batch.batch_id,
            "platform": batch.platform,
            "execution_mode": batch.execution_mode,
            "status": batch.status,
            "stop_policy": batch.stop_policy,
            "capture_evidence": batch.capture_evidence,
            "source_read_batch_id": batch.source_read_batch_id,
            "source_snapshot_sha256": batch.source_snapshot_sha256,
            "source_page_context_sha256": batch.source_page_context_sha256,
            "normalized_request_digest": batch.normalized_request_digest,
            "created_by": batch.created_by,
            "created_at": _iso(batch.created_at),
            "started_at": _iso(batch.started_at),
            "completed_at": _iso(batch.completed_at),
        },
        "run_ids": all_run_ids,
        "id_sets": {
            "item_ids": [item["item_id"] for item in item_reports],
            "task_ids": [item["task_id"] for item in item_reports],
            "review_task_ids": [item["review_task_id"] for item in item_reports],
            "operation_ids": [item["operation_id"] for item in item_reports],
            "execution_attempt_ids": [
                attempt["execution_attempt_id"]
                for item in item_reports
                for attempt in item["attempts"]
            ],
        },
        "items": item_reports,
        "count_identity": {
            "recorded": recorded_counts,
            "recomputed": counts,
            "formula": (
                f"{counts['processed_count']} = {counts['previewed_count']} + "
                f"{counts['verified_count']} + {counts['failed_count']} + "
                f"{counts['skipped_count']} + {counts['cancelled_count']} + "
                f"{counts['needs_reconciliation_count']}"
            ),
            "passed": recorded_counts == counts,
        },
        "database_readback": {
            "passed": not database_errors,
            "errors": database_errors,
            "batch_item_count": len(items),
        },
        "importer_archive": {
            "queue_dir": str(queue_dir) if queue_dir is not None else "",
            "check_requested": queue_dir is not None,
        },
    }
    validation = validate_price_batch_acceptance(payload)
    payload["validation"] = validation
    payload["overall_status"] = (
        "PASSED"
        if validation["passed"]
        and batch.status == "COMPLETED"
        and counts["failed_count"] == 0
        and counts["needs_reconciliation_count"] == 0
        and counts["processed_count"] == counts["total_count"]
        else "NOT_PASSED"
    )
    return payload


def validate_price_batch_acceptance(payload: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    batch = payload.get("batch")
    items = payload.get("items")
    if payload.get("contract_version") != 3 or not isinstance(batch, Mapping):
        return {"passed": False, "errors": ["invalid task 12 batch contract"]}
    if not isinstance(items, list) or not items:
        return {"passed": False, "errors": ["items must be a non-empty array"]}
    if any(not isinstance(item, Mapping) for item in items):
        return {"passed": False, "errors": ["every item must be an object"]}
    ordered = sorted(items, key=lambda item: item.get("ordinal", 0))
    if [item.get("ordinal") for item in ordered] != list(range(1, len(items) + 1)) or ordered != items:
        errors.append("item ordinals are not contiguous and ordered")
    statuses = [str(item.get("status") or "") for item in items]
    allowed_statuses = {status.value for status in BatchItemStatus}
    if any(status not in allowed_statuses for status in statuses):
        errors.append("item status contains an unknown value")
    try:
        recomputed = aggregate_batch_counts(statuses)
    except ValidationError as exc:
        errors.append(str(exc))
        recomputed = {}
    count_identity = payload.get("count_identity")
    if not isinstance(count_identity, Mapping) or count_identity.get("recomputed") != recomputed:
        errors.append("count identity does not match item statuses")
    for field in ("item_id", "task_id", "review_task_id", "operation_id", "page_identity_key", "write_identity_key"):
        values = [str(item.get(field) or "") for item in items]
        if any(not value for value in values) or len(values) != len(set(values)):
            errors.append(f"{field} set is empty or not unique")
    attempt_ids: list[str] = []
    derived_run_ids: list[str] = []
    archive_requested = bool(payload.get("importer_archive", {}).get("check_requested")) if isinstance(payload.get("importer_archive"), Mapping) else False
    for item in items:
        ordinal = item.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int):
            errors.append(f"{item.get('item_id')}: ordinal must be an integer")
        if item.get("batch_id") != batch.get("batch_id"):
            errors.append(f"{item.get('item_id')}: batch binding mismatch")
        for price_name in ("approved_old_price", "fresh_old_price", "target_price", "post_price"):
            value = item.get(price_name)
            if value is None:
                continue
            if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.[0-9]{2}", value):
                errors.append(f"{item.get('item_id')}: {price_name} must be a two-decimal string")
        for hash_name in (
            "approved_payload_hash",
            "page_identity_key",
            "write_identity_key",
            "source_snapshot_sha256",
            "source_page_context_sha256",
        ):
            if not _SHA256_RE.fullmatch(str(item.get(hash_name) or "")):
                errors.append(f"{item.get('item_id')}: invalid {hash_name}")
        attempts = item.get("attempts")
        if not isinstance(attempts, list):
            errors.append(f"{item.get('item_id')}: attempts must be an array")
            continue
        for attempt in attempts:
            if not isinstance(attempt, Mapping):
                errors.append(f"{item.get('item_id')}: invalid attempt")
                continue
            attempt_id = str(attempt.get("execution_attempt_id") or "")
            if not attempt_id:
                errors.append(f"{item.get('item_id')}: empty attempt id")
            attempt_ids.append(attempt_id)
            run_id = str(attempt.get("shadowbot_run_id") or "")
            if run_id and run_id not in derived_run_ids:
                derived_run_ids.append(run_id)
            if attempt.get("ended_at") and (
                not attempt.get("result_id")
                or not _SHA256_RE.fullmatch(str(attempt.get("result_file_sha256") or ""))
            ):
                errors.append(f"{attempt_id}: terminal attempt lacks importer identity")
            if archive_requested and attempt.get("ended_at") and not bool(attempt.get("archive", {}).get("passed")):
                errors.append(f"{attempt_id}: archive verification failed")
    if len(attempt_ids) != len(set(attempt_ids)):
        errors.append("execution attempt ids cross product boundaries")
    run_ids = payload.get("run_ids")
    if not isinstance(run_ids, list) or any(not isinstance(value, str) or not value for value in run_ids):
        errors.append("run_ids must be an array of non-empty strings")
    elif len(run_ids) != len(set(run_ids)) or run_ids != derived_run_ids:
        errors.append("run id set does not match item attempts")
    expected_id_sets = {
        "item_ids": [str(item.get("item_id") or "") for item in items],
        "task_ids": [str(item.get("task_id") or "") for item in items],
        "review_task_ids": [str(item.get("review_task_id") or "") for item in items],
        "operation_ids": [str(item.get("operation_id") or "") for item in items],
        "execution_attempt_ids": attempt_ids,
    }
    if payload.get("id_sets") != expected_id_sets:
        errors.append("top-level id sets do not match item records")
    database = payload.get("database_readback")
    if not isinstance(database, Mapping) or not bool(database.get("passed")):
        errors.append("database readback failed")
    return {
        "passed": not errors,
        "errors": errors,
        "unique_attempt_id_count": len(set(attempt_ids)),
        "no_cross_product_attempt_binding": len(attempt_ids) == len(set(attempt_ids)),
        "counts_recomputed": recomputed,
    }


def render_price_batch_markdown(payload: Mapping[str, Any]) -> str:
    batch = payload.get("batch") if isinstance(payload.get("batch"), Mapping) else {}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    counts = payload.get("count_identity") if isinstance(payload.get("count_identity"), Mapping) else {}
    validation = payload.get("validation") if isinstance(payload.get("validation"), Mapping) else {}
    database = payload.get("database_readback") if isinstance(payload.get("database_readback"), Mapping) else {}
    archive_requested = bool(payload.get("importer_archive", {}).get("check_requested")) if isinstance(payload.get("importer_archive"), Mapping) else False
    passed = payload.get("overall_status") == "PASSED"
    lines = [
        "# 任务12多商品串行改价报告",
        "",
        f"**结论：{'本次批次通过。' if passed else '本次批次尚未通过。'}**",
        "",
        f"批次 `{batch.get('batch_id', '-')}` 在 `{batch.get('execution_mode', '-')}` 模式下处理 {len(items)} 个商品，当前批次状态为 `{batch.get('status', '-')}`。",
        "",
        "## 批次与计数",
        "",
        f"- 平台键：`{batch.get('platform', '-')}`",
        f"- 来源读取批次：`{batch.get('source_read_batch_id', '-')}`",
        f"- 运行 ID：{', '.join(f'`{value}`' for value in payload.get('run_ids', [])) or '-'}",
        f"- 计数恒等式：`{counts.get('formula', '-')}`；校验{'通过' if counts.get('passed') else '未通过'}。",
        "",
        "## 逐商品结果",
        "",
    ]
    for item in items:
        attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
        attempt_ids = ", ".join(f"`{attempt.get('execution_attempt_id', '-')}`" for attempt in attempts) or "-"
        run_ids = ", ".join(f"`{attempt.get('shadowbot_run_id', '-')}`" for attempt in attempts if attempt.get("shadowbot_run_id")) or "-"
        lines.extend(
            [
                f"### {item.get('ordinal', '-')}. {item.get('product_name', '-')} {item.get('grade', '-')}",
                "",
                f"- 结果：`{item.get('status', '-')}`；前价 `{item.get('approved_old_price', '-')}`，目标价 `{item.get('target_price', '-')}`，后价 `{item.get('post_price') or '-'}`。",
                f"- 错误：{item.get('error_code') or '无'}{('；' + str(item.get('error_message'))) if item.get('error_message') else ''}",
                f"- 任务/复核/操作：`{item.get('task_id', '-')}` / `{item.get('review_task_id', '-')}` / `{item.get('operation_id', '-')}`",
                f"- Attempt ID：{attempt_ids}",
                f"- 商品运行 ID：{run_ids}",
                f"- 批准载荷哈希：`{item.get('approved_payload_hash', '-')}`；结果哈希：`{item.get('result_hash') or '-'}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Importer、归档与数据库回读",
            "",
            f"- Result Importer 身份：终态 attempt 必须同时具有 result ID 与结果 SHA-256；验收器结果为{'通过' if validation.get('passed') else '未通过'}。",
            f"- 归档校验：{'已逐 attempt 校验请求、结果和 sidecar' if archive_requested else '本次未提供 queue_dir，未执行文件归档校验'}。",
            f"- 数据库回读：{'通过' if database.get('passed') else '未通过'}；逐商品记录 {database.get('batch_item_count', 0)} 条。",
            f"- 无跨商品副作用账本：{'通过' if validation.get('no_cross_product_attempt_binding') else '未通过'}（每个 attempt 仅归属一个 operation/item）。",
            "",
            "## 关键哈希",
            "",
            f"- 规范化请求摘要：`{batch.get('normalized_request_digest', '-')}`",
            f"- 来源快照：`{batch.get('source_snapshot_sha256', '-')}`",
            f"- 页面上下文：`{batch.get('source_page_context_sha256', '-')}`",
            "",
        ]
    )
    if validation.get("errors"):
        lines.extend(["## 简短错误报告", ""])
        lines.extend(f"- {error}" for error in validation["errors"])
        lines.append("")
    return "\n".join(lines)


def write_price_batch_reports(
    payload: Mapping[str, Any],
    *,
    json_path: Path,
    markdown_path: Path,
) -> tuple[Path, Path]:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(
        render_price_batch_markdown(payload),
        encoding="utf-8",
        newline="\n",
    )
    return json_path, markdown_path


def _archive_state(queue_dir: Path | None, attempt_id: str) -> dict[str, Any]:
    if queue_dir is None:
        return {"checked": False, "passed": False, "archive_dir": ""}
    archive_dir = Path(queue_dir) / "archive" / attempt_id
    request_paths = list(archive_dir.glob("*.ready.json")) + list(archive_dir.glob("*.request.json"))
    result_paths = list(archive_dir.glob("*.result.json"))
    checked_paths = request_paths + result_paths
    passed = bool(request_paths and result_paths) and all(_sidecar_matches(path) for path in checked_paths)
    return {
        "checked": True,
        "passed": passed,
        "archive_dir": str(archive_dir),
        "request_count": len(request_paths),
        "result_count": len(result_paths),
    }


def _sidecar_matches(path: Path) -> bool:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        return False
    expected = sidecar.read_text(encoding="ascii").strip().lower()
    return expected == hashlib.sha256(path.read_bytes()).hexdigest()


def _prefixed_hash(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    return raw if raw.startswith("sha256:") else "sha256:" + raw


def _iso(value: Any) -> str:
    return value.isoformat() if value is not None else ""

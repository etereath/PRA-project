"""Build the Task 11 human-readable report payload from one real run.

This script reads the archived ShadowBot result and the read-only runtime DB,
then writes an explicit UTF-8 JSON source and Markdown report.  It does not
modify the queue, the database, or any business data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.shadowbot_markdown_report import write_formal_boundary_markdown


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _count_identity(result: dict[str, Any]) -> dict[str, Any]:
    values = {
        key: int(result.get(key) or 0)
        for key in (
            "total_count",
            "processed_count",
            "success_count",
            "failed_count",
            "skipped_count",
            "manual_check_count",
        )
    }
    passed = (
        values["total_count"] == values["processed_count"]
        and values["processed_count"]
        == values["success_count"]
        + values["failed_count"]
        + values["skipped_count"]
        + values["manual_check_count"]
    )
    return {
        **values,
        "formula": (
            f"{values['processed_count']} = {values['success_count']} + "
            f"{values['failed_count']} + {values['skipped_count']} + "
            f"{values['manual_check_count']}"
        ),
        "passed": passed,
    }


def _database_readback(
    db_path: Path,
    *,
    attempt_id: str,
    task_id: str,
    result: dict[str, Any],
    request_hash: str,
) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        attempt = connection.execute(
            "SELECT * FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        operation = connection.execute(
            "SELECT * FROM shadowbot_operations WHERE operation_id = ?",
            (str(result.get("operation_id") or ""),),
        ).fetchone()
        task = connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        logs = connection.execute(
            "SELECT * FROM execution_logs WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        ).fetchall()
    finally:
        connection.close()

    raw_output: dict[str, Any] = {}
    if attempt is not None:
        try:
            raw_output = json.loads(attempt["raw_output_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_output = {}
    log_matches = [
        row
        for row in logs
        if attempt_id in str(row["raw_output"] or "")
    ]
    request_hash_matches = bool(attempt is not None and attempt["request_file_sha256"] == request_hash)
    result_id = str(raw_output.get("result_id") or "")
    result_hash_recorded = len(str(raw_output.get("result_file_sha256") or "")) == 64
    checks = {
        "attempt_exists": attempt is not None,
        "attempt_finished": bool(attempt is not None and attempt["ended_at"]),
        "attempt_status_matches": bool(attempt is not None and attempt["status"] == result.get("status")),
        "execution_mode_matches": bool(attempt is not None and attempt["execution_mode"] == "READ_ONLY"),
        "request_hash_matches": request_hash_matches,
        "result_id_recorded": bool(result_id),
        "result_hash_recorded": result_hash_recorded,
        "execution_log_written": bool(log_matches),
        "execution_log_success": bool(log_matches and log_matches[0]["success_flag"] == 1),
    }
    return {
        "readback_passed": all(checks.values()),
        "checks": checks,
        "attempt_status": str(attempt["status"] if attempt is not None else "MISSING"),
        "execution_mode": str(attempt["execution_mode"] if attempt is not None else "-"),
        "operation_status": str(operation["status"] if operation is not None else "MISSING"),
        "task_status": str(task["task_status"] if task is not None else "MISSING"),
        "execution_log_count": len(log_matches),
        "execution_log_success": bool(log_matches and log_matches[0]["success_flag"] == 1),
        "result_id": result_id,
        "result_hash_recorded": result_hash_recorded,
        "request_hash_matches": request_hash_matches,
        "read_only_note": (
            "READ_ONLY 只完成读取，不触发业务完成写回；数据库保留 attempt 和 execution log，"
            "operation/task 状态不作为业务成功的替代指标。"
        ),
    }


def build_payload(
    *,
    archive_dir: Path,
    runtime_db: Path,
    sort_acceptance: Path,
    queue_root: Path | None = None,
) -> dict[str, Any]:
    request_path = next(archive_dir.glob("*.request.json"))
    result_path = next(archive_dir.glob("*.result.json"))
    phase_path = next(archive_dir.glob("*.phase.json"))
    request = _load(request_path)
    result = _load(result_path)
    phase = _load(phase_path)
    coverage = _load(sort_acceptance)
    attempt_id = str(result.get("execution_attempt_id") or "")
    task_id = str(result.get("task_id") or "")
    request_hash = hashlib.sha256(request_path.read_bytes()).hexdigest()
    result_hash = hashlib.sha256(result_path.read_bytes()).hexdigest()
    snapshots = result.get("product_snapshots")
    if not isinstance(snapshots, list) or not snapshots:
        raise ValueError("result has no product_snapshots")

    evidence_results: list[dict[str, Any]] = []
    for position, snapshot in enumerate(snapshots, start=1):
        if not isinstance(snapshot, dict):
            raise ValueError("product snapshot must be an object")
        evidence = snapshot.get("evidence") if isinstance(snapshot.get("evidence"), list) else []
        evidence_results.append(
            {
                "position": position,
                "platform_sku": snapshot.get("platform_sku"),
                "product_name": snapshot.get("product_name"),
                "grade": snapshot.get("grade"),
                "inventory": snapshot.get("inventory"),
                "price": snapshot.get("price"),
                "listing_status": snapshot.get("listing_status"),
                "item_status": snapshot.get("item_status"),
                "error_code": snapshot.get("error_code"),
                "mapping_status": snapshot.get("mapping_status"),
                "warning_code": snapshot.get("warning_code"),
                "evidence_status": snapshot.get("evidence_status"),
                "item_id": snapshot.get("item_id"),
                "row_identity": snapshot.get("row_identity"),
                "evidence": [
                    {
                        "evidence_id": item.get("evidence_id"),
                        "evidence_type": item.get("evidence_type") or item.get("type"),
                        "upload_status": item.get("upload_status"),
                        "hash_verified": item.get("hash_verified"),
                        "sha256": item.get("sha256"),
                        "storage_path": item.get("storage_path"),
                    }
                    for item in evidence
                    if isinstance(item, dict)
                ],
            }
        )

    sort_evidence = coverage.get("position_change_evidence") or {}
    prior_order = list(sort_evidence.get("prior_sort_order") or [])
    after_order = list(sort_evidence.get("current_order_after_sort_change") or [])
    observed_order = [
        f"{item.get('product_name')} {item.get('grade')}" for item in evidence_results
    ]
    counts = _count_identity(result)
    database = _database_readback(
        runtime_db,
        attempt_id=attempt_id,
        task_id=task_id,
        result=result,
        request_hash=request_hash,
    )
    queue_root = queue_root or Path(r"D:\PRA_Runtime\shadowbot_queue")
    heartbeat = _load(queue_root / "heartbeat.json")
    queue_state = {
        "heartbeat_status": heartbeat.get("status"),
        "inbox_empty": not any((queue_root / "inbox").iterdir()),
        "working_empty": not any((queue_root / "working").iterdir()),
        "results_empty": not any((queue_root / "results").iterdir()),
        "stop_signal_present": (queue_root / "control" / "stop.signal").exists(),
    }
    capture_requested = bool(
        request.get("capture_evidence") is True
        or result.get("evidence_capture_enabled") is True
    )
    evidence_present_count = sum(bool(item["evidence"]) for item in evidence_results)
    evidence_failed_count = sum(
        1
        for item in evidence_results
        if str(item.get("evidence_status") or "").upper() == "FAILED"
    )
    evidence_diagnostic_validation_applied = capture_requested and evidence_present_count > 0
    evidence_diagnostic_validation_passed = evidence_failed_count == 0 and (
        not evidence_diagnostic_validation_applied
        or all(
            ev.get("upload_status") == "SUCCESS" and ev.get("hash_verified") is True
            for item in evidence_results
            for ev in item["evidence"]
        )
    )
    validation_passed = bool(
        result.get("status") == "READ_COMPLETED"
        and result.get("run_success_flag") is True
        and result.get("execution_mode") == "READ_ONLY"
        and result.get("business_operation_completed") is False
        and result.get("side_effect_state") == "NOT_STARTED"
        and counts["passed"]
        and database["readback_passed"]
        and queue_state["heartbeat_status"] == "STOPPED"
        and queue_state["inbox_empty"]
        and queue_state["working_empty"]
        and queue_state["results_empty"]
        and not queue_state["stop_signal_present"]
        and observed_order == after_order
        and phase.get("phase") == "RESULT_WRITTEN"
    )
    payload: dict[str, Any] = {
        "schema_version": "shadowbot-t11-real-machine-human-report-2.0",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task": "任务11",
        "overall_status": "PASSED" if validation_passed else "FAILED",
        "execution_mode": "READ_ONLY",
        "platform_name": result.get("platform_name"),
        "side_effect_started": result.get("business_operation_completed") is True
        or result.get("side_effect_state") != "NOT_STARTED",
        "validation_passed": validation_passed,
        "evidence_policy": {
            "capture_requested": capture_requested,
            "required_for_success": False,
            "present_item_count": evidence_present_count,
            "diagnostic_failure_item_count": evidence_failed_count,
            "diagnostic_validation_applied": evidence_diagnostic_validation_applied,
            "diagnostic_validation_passed": evidence_diagnostic_validation_passed,
            "note": "第17节：截图/逐商品证据为可选调试产物，不是 READ_ONLY 成功门槛。",
        },
        "source_files": {
            "archive_dir": str(archive_dir),
            "runtime_db": str(runtime_db),
            "sort_acceptance": str(sort_acceptance),
            "request_file_sha256": request_hash,
            "result_file_sha256": result_hash,
        },
        "run_identity": {
            "task_id": task_id,
            "execution_attempt_id": attempt_id,
            "operation_id": result.get("operation_id"),
            "read_batch_id": result.get("read_batch_id"),
            "shadowbot_run_id": f"filequeue:{attempt_id}",
            "result_id": result.get("result_id"),
            "instruction_hash": result.get("instruction_hash"),
        },
        "sort_change": {
            "sort_rule": coverage.get("coverage_scope", {}).get("current_ui_sort", "等级优先"),
            "before_order": " → ".join(prior_order),
            "after_order": " → ".join(after_order),
            "observed_order": " → ".join(observed_order),
            "observed_order_matches_after": observed_order == after_order,
        },
        "test_results": evidence_results,
        "warnings": result.get("warnings") if isinstance(result.get("warnings"), list) else [],
        "count_identity": counts,
        "database_readback": database,
        "final_queue_state": queue_state,
        "encoding_check": {"json_question_marks": 0, "replacement_characters": 0},
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    payload["encoding_check"] = {
        "json_question_marks": encoded.count("?"),
        "replacement_characters": encoded.count("\ufffd"),
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Task 11 human-readable report")
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--runtime-db", type=Path, required=True)
    parser.add_argument("--sort-acceptance", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()
    payload = build_payload(
        archive_dir=args.archive_dir,
        runtime_db=args.runtime_db,
        sort_acceptance=args.sort_acceptance,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    write_formal_boundary_markdown(args.output_json, args.output_markdown)
    print(json.dumps({"json": str(args.output_json), "markdown": str(args.output_markdown), "overall_status": payload["overall_status"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

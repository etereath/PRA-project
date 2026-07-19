from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository


SUCCESS_STATUSES = {
    "READ_ONLY": {"READ_COMPLETED"},
    "FILL_PREVIEW": {"PREVIEW_COMPLETED"},
    "COMMIT": {"SUCCESS", "ALREADY_APPLIED"},
    "RECONCILE": {"VERIFIED", "NOT_APPLIED", "START_UNKNOWN", "SIDE_EFFECT_UNKNOWN"},
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify a real ShadowBot file-queue acceptance attempt")
    parser.add_argument("--runtime-db", type=Path, required=True)
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--execution-attempt-id", required=True)
    parser.add_argument("--execution-mode", choices=sorted(SUCCESS_STATUSES), required=True)
    parser.add_argument("--profile", choices=("NORMAL", "PRE_SUBMIT_STOP"), default="NORMAL")
    parser.add_argument("--allow-local-evidence", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = verify_acceptance(
        runtime_db=args.runtime_db,
        queue_dir=args.queue_dir,
        execution_attempt_id=args.execution_attempt_id,
        execution_mode=args.execution_mode,
        require_shared_evidence=not args.allow_local_evidence,
        profile=args.profile,
    )
    content = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(content, encoding="utf-8")
    print(content, end="")
    return 0 if report["ok"] else 1


def verify_acceptance(
    *,
    runtime_db: Path,
    queue_dir: Path,
    execution_attempt_id: str,
    execution_mode: str,
    require_shared_evidence: bool = True,
    profile: str = "NORMAL",
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, condition: bool, detail: Any = "") -> None:
        checks.append({"name": name, "ok": bool(condition), "detail": detail})

    repository = SQLiteRuntimeRepository(runtime_db)
    repository.init_schema()
    attempt = repository.get_shadowbot_execution_attempt(execution_attempt_id)
    check("attempt_exists", attempt is not None, execution_attempt_id)
    if attempt is None:
        return _report(execution_attempt_id, execution_mode, checks)

    check("execution_mode_matches", attempt.execution_mode == execution_mode, attempt.execution_mode)
    check("attempt_finished", attempt.ended_at is not None, str(attempt.ended_at or ""))
    check("instruction_hash_recorded", attempt.instruction_hash.startswith("sha256:"), attempt.instruction_hash)
    check("request_sha256_recorded", len(attempt.request_file_sha256) == 64, attempt.request_file_sha256)
    check("queue_request_path_recorded", bool(attempt.queue_request_path), attempt.queue_request_path)

    archive_dir = queue_dir / "archive" / execution_attempt_id
    check("archive_directory_exists", archive_dir.is_dir(), str(archive_dir))
    if not archive_dir.is_dir():
        return _report(execution_attempt_id, execution_mode, checks, attempt=attempt)

    request_paths = sorted(archive_dir.glob("*.request.json"))
    result_paths = sorted(archive_dir.glob("*.result.json"))
    phase_paths = sorted(archive_dir.glob("*.phase.json"))
    check("one_archived_request", len(request_paths) == 1, [str(path) for path in request_paths])
    check("one_archived_result", len(result_paths) == 1, [str(path) for path in result_paths])
    check("one_archived_phase", len(phase_paths) == 1, [str(path) for path in phase_paths])
    if len(request_paths) != 1 or len(result_paths) != 1:
        return _report(execution_attempt_id, execution_mode, checks, attempt=attempt)

    request_path = request_paths[0]
    result_path = result_paths[0]
    request_bytes = request_path.read_bytes()
    result_bytes = result_path.read_bytes()
    request_hash = hashlib.sha256(request_bytes).hexdigest()
    check("request_checksum_valid", _checksum_valid(request_path, request_bytes), str(request_path))
    check("result_checksum_valid", _checksum_valid(result_path, result_bytes), str(result_path))
    check("request_hash_matches_database", request_hash == attempt.request_file_sha256, request_hash)

    request = _json_object(request_bytes, request_path)
    result = _json_object(result_bytes, result_path)
    for field_name in (
        "task_id",
        "operation_id",
        "execution_attempt_id",
        "execution_mode",
        "instruction_hash",
    ):
        check(
            f"binding_{field_name}",
            str(request.get(field_name) or "") == str(result.get(field_name) or ""),
            {"request": request.get(field_name), "result": result.get(field_name)},
        )
    check("result_request_hash_matches", result.get("request_file_sha256") == request_hash, request_hash)
    check("result_mode_matches", result.get("execution_mode") == execution_mode, result.get("execution_mode"))
    expected_statuses = {"FAILED"} if profile == "PRE_SUBMIT_STOP" else SUCCESS_STATUSES[execution_mode]
    check("result_status_accepted", result.get("status") in expected_statuses, result.get("status"))
    if profile == "PRE_SUBMIT_STOP":
        check("technical_run_stopped", result.get("run_success_flag") is False, result.get("run_success_flag"))
        check("stop_error_code", result.get("error_code") == "WORKER_STOP_REQUESTED", result.get("error_code"))
        check("stop_is_retryable", result.get("retryable") is True, result.get("retryable"))
        check("no_business_completion", result.get("business_operation_completed") is False, result.get("business_operation_completed"))
        check("no_side_effect", result.get("side_effect_state") == "NOT_STARTED", result.get("side_effect_state"))
    else:
        check("technical_run_succeeded", result.get("run_success_flag") is True, result.get("run_success_flag"))

    if profile == "NORMAL" and execution_mode == "READ_ONLY":
        check("actual_price_recorded", bool(result.get("actual_price") or result.get("old_price")), result.get("actual_price") or result.get("old_price"))
    if profile == "NORMAL" and execution_mode == "FILL_PREVIEW":
        check(
            "preview_old_price_matches_request",
            str(result.get("old_price") or "") == str(request.get("expected_old_price") or ""),
            {"result": result.get("old_price"), "request": request.get("expected_old_price")},
        )
        check(
            "preview_readback_matches_target",
            str(result.get("input_price_readback") or "") == str(request.get("target_price") or ""),
            {"result": result.get("input_price_readback"), "request": request.get("target_price")},
        )
    if profile == "NORMAL" and execution_mode in {"READ_ONLY", "FILL_PREVIEW"}:
        check("no_business_completion", result.get("business_operation_completed") is False, result.get("business_operation_completed"))
        check("no_side_effect", result.get("side_effect_state") == "NOT_STARTED", result.get("side_effect_state"))
    elif profile == "NORMAL" and execution_mode == "COMMIT":
        check("business_completed", result.get("business_operation_completed") is True, result.get("business_operation_completed"))
        check("side_effect_verified", result.get("side_effect_state") == "VERIFIED", result.get("side_effect_state"))
        check(
            "commit_actual_price_matches_target",
            str(result.get("actual_price") or "") == str(request.get("target_price") or ""),
            {"result": result.get("actual_price"), "request": request.get("target_price")},
        )

    if phase_paths:
        phase = json.loads(phase_paths[0].read_text(encoding="utf-8-sig"))
        check("phase_result_written", phase.get("phase") == "RESULT_WRITTEN", phase.get("phase"))
        check("phase_worker_recorded", bool(phase.get("worker_id")), phase.get("worker_id"))

    # Task 11 contract v2 follows Section 17: screenshots are optional
    # diagnostics, so the verifier must not make them a READ_ONLY gate.
    is_v2_multi_product_read = (
        execution_mode == "READ_ONLY"
        and result.get("contract_version") == 2
        and isinstance(result.get("product_snapshots"), list)
    )
    evidence = result.get("evidence")
    if profile == "NORMAL" and not is_v2_multi_product_read:
        check("evidence_present", isinstance(evidence, list) and bool(evidence), len(evidence) if isinstance(evidence, list) else 0)
    if profile == "NORMAL" and not is_v2_multi_product_read and isinstance(evidence, list):
        for index, item in enumerate(evidence, start=1):
            if not isinstance(item, dict):
                check(f"evidence_{index}_object", False, type(item).__name__)
                continue
            check(f"evidence_{index}_sha256", len(str(item.get("sha256") or "")) == 64, item.get("sha256"))
            check(f"evidence_{index}_captured_at", bool(item.get("captured_at")), item.get("captured_at"))
            if require_shared_evidence:
                storage_path = Path(str(item.get("storage_path") or ""))
                storage_exists = storage_path.is_file()
                check(f"evidence_{index}_upload_success", item.get("upload_status") == "SUCCESS", item.get("upload_status"))
                check(f"evidence_{index}_hash_verified", item.get("hash_verified") is True, item.get("hash_verified"))
                check(f"evidence_{index}_storage_exists", storage_exists, str(storage_path))
                if storage_exists:
                    storage_hash = hashlib.sha256(storage_path.read_bytes()).hexdigest()
                    check(
                        f"evidence_{index}_storage_hash",
                        storage_hash == item.get("storage_sha256") == item.get("sha256"),
                        storage_hash,
                    )
    elif profile == "NORMAL" and is_v2_multi_product_read:
        check("v2_evidence_optional", True, "Section 17: structured READ_ONLY success does not require screenshots")

    active_files = []
    for directory in ("inbox", "working", "results"):
        active_files.extend(str(path) for path in (queue_dir / directory).glob(f"{execution_attempt_id}*"))
    check("no_active_queue_files", not active_files, active_files)
    quarantine_files = [str(path) for path in (queue_dir / "quarantine").glob(f"*{execution_attempt_id}*")]
    check("not_quarantined", not quarantine_files, quarantine_files)

    logs = repository.list_execution_logs(task_id=str(request.get("task_id") or ""))
    matching_logs = [log for log in logs if execution_attempt_id in log.raw_output]
    check("execution_log_written", bool(matching_logs), len(matching_logs))
    report = _report(execution_attempt_id, execution_mode, checks, attempt=attempt, result=result)
    report["profile"] = profile
    return report


def _checksum_valid(path: Path, content: bytes) -> bool:
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    if not checksum_path.is_file():
        return False
    expected = checksum_path.read_text(encoding="ascii").strip().lower()
    return expected == hashlib.sha256(content).hexdigest()


def _json_object(content: bytes, path: Path) -> dict[str, Any]:
    data = json.loads(content.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return data


def _report(
    execution_attempt_id: str,
    execution_mode: str,
    checks: list[dict[str, Any]],
    *,
    attempt: Any = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failed = [item["name"] for item in checks if not item["ok"]]
    return {
        "schema_version": "shadowbot-filequeue-acceptance-1.0",
        "execution_attempt_id": execution_attempt_id,
        "execution_mode": execution_mode,
        "ok": not failed,
        "failed_checks": failed,
        "status": (result or {}).get("status") or getattr(attempt, "status", ""),
        "checks": checks,
    }


if __name__ == "__main__":
    raise SystemExit(main())

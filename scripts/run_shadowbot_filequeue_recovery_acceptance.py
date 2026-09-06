from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_executor import ShadowBotFileQueueRunner
from app.services.shadowbot_queue import ShadowBotQueueWatchdog, ShadowBotResultImporter
from scripts.prepare_shadowbot_e2e_chain import prepare_shadowbot_chain_from_args


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run isolated ShadowBot file-queue recovery acceptance")
    parser.add_argument("--runtime-db", type=Path, required=True)
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_recovery_acceptance(args.runtime_db, args.queue_dir)
    content = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(content, encoding="utf-8")
    print(content, end="")
    return 0 if report["ok"] else 1


def run_recovery_acceptance(runtime_db: Path, queue_dir: Path) -> dict[str, object]:
    _require_clean_target(runtime_db, queue_dir)
    previous = {
        "SHADOWBOT_RUNNER_TYPE": os.environ.get("SHADOWBOT_RUNNER_TYPE"),
        "SHADOWBOT_QUEUE_DIR": os.environ.get("SHADOWBOT_QUEUE_DIR"),
    }
    os.environ["SHADOWBOT_RUNNER_TYPE"] = "filequeue"
    os.environ["SHADOWBOT_QUEUE_DIR"] = str(queue_dir)
    try:
        prepared = prepare_shadowbot_chain_from_args(
            argparse.Namespace(
                runtime_db=runtime_db,
                platform="蚂蚁花团供应商",
                sku="SKU-RECOVERY-ACCEPTANCE",
                platform_sku="SKU-RECOVERY-ACCEPTANCE",
                product_name="恢复验收虚拟商品",
                grade="TEST",
                expected_old_price="9.80",
                target_price="10.30",
                execution_mode="COMMIT",
                task_id="TASK-RECOVERY-ACCEPTANCE",
                approval_id="REVIEW-RECOVERY-ACCEPTANCE",
                operation_id="OP-RECOVERY-ACCEPTANCE",
                execution_attempt_id="ATTEMPT-RECOVERY-UNKNOWN",
                trade_date="",
                approved_by="filequeue-recovery-acceptance",
                approval_ttl_minutes=60,
                source_read_attempt_id="SYNTHETIC-RECOVERY-ACCEPTANCE",
                start=True,
            )
        )
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    repository = SQLiteRuntimeRepository(runtime_db)
    runner = ShadowBotFileQueueRunner(queue_dir)
    source_request, source_hash = _claim(queue_dir, prepared.execution_attempt_id)
    stale_at = datetime.now(UTC) - timedelta(minutes=5)
    _write_json(
        queue_dir / "working" / f"{prepared.execution_attempt_id}.phase.json",
        {
            "task_id": source_request["task_id"],
            "operation_id": source_request["operation_id"],
            "execution_attempt_id": source_request["execution_attempt_id"],
            "execution_mode": "COMMIT",
            "phase": "SUBMIT_CLICKED",
            "side_effect_state": "SUBMIT_CLICKED",
            "request_file_sha256": source_hash,
            "instruction_hash": source_request["instruction_hash"],
            "worker_id": "SYNTHETIC-RECOVERY-WORKER",
            "updated_at": stale_at.isoformat(),
        },
    )
    _write_json(
        queue_dir / "heartbeat.json",
        {
            "worker_id": "SYNTHETIC-RECOVERY-WORKER",
            "status": "RUNNING",
            "updated_at": stale_at.isoformat(),
        },
    )

    watchdog_events = ShadowBotQueueWatchdog(queue_dir, stale_seconds=30).inspect(now=datetime.now(UTC))
    importer = ShadowBotResultImporter(repository, runner, queue_dir)
    import_events = importer.import_available()
    reconcile_id = "RECONCILE-" + hashlib.sha256(prepared.execution_attempt_id.encode("utf-8")).hexdigest()[:20]
    reconcile_attempt = repository.get_shadowbot_execution_attempt(reconcile_id)

    reconcile_request, reconcile_hash = _claim(queue_dir, reconcile_id)
    _write_json(
        queue_dir / "working" / f"{reconcile_id}.phase.json",
        {
            "execution_attempt_id": reconcile_id,
            "execution_mode": "RECONCILE",
            "phase": "RESULT_WRITTEN",
            "side_effect_state": "NOT_APPLIED",
            "request_file_sha256": reconcile_hash,
            "instruction_hash": reconcile_request["instruction_hash"],
            "worker_id": "SYNTHETIC-RECONCILE-WORKER",
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )
    _publish_result(
        queue_dir,
        reconcile_request,
        reconcile_hash,
        {
            "status": "NOT_APPLIED",
            "run_success_flag": True,
            "business_operation_completed": False,
            "side_effect_state": "NOT_APPLIED",
            "error_code": "SUBMIT_NOT_APPLIED",
            "error_message": "synthetic reconcile confirmed original price",
            "retryable": False,
            "old_price": "9.80",
            "actual_price": "9.80",
            "target_price": "10.30",
            "evidence": [],
        },
    )
    reconcile_import_events = importer.import_available()

    source_attempt = repository.get_shadowbot_execution_attempt(prepared.execution_attempt_id)
    final_reconcile = repository.get_shadowbot_execution_attempt(reconcile_id)
    operation = repository.get_shadowbot_operation(prepared.operation_id)
    reconcile_attempts = [
        item
        for item in repository.list_shadowbot_execution_attempts(operation_id=prepared.operation_id)
        if item.execution_mode == "RECONCILE"
    ]
    active_files = []
    for directory in ("inbox", "working", "results"):
        active_files.extend(str(path) for path in (queue_dir / directory).glob("*"))
    checks = {
        "watchdog_wrote_unknown_result": bool(watchdog_events) and watchdog_events[0].get("status") == "RECOVERY_RESULT_WRITTEN",
        "source_result_imported": bool(import_events) and import_events[0].get("status") == "IMPORTED",
        "source_needs_reconciliation": source_attempt is not None and source_attempt.status == "SIDE_EFFECT_UNKNOWN" and source_attempt.side_effect_state == "UNKNOWN",
        "one_deterministic_reconcile": reconcile_attempt is not None and len(reconcile_attempts) == 1,
        "reconcile_result_imported": bool(reconcile_import_events) and reconcile_import_events[0].get("status") == "IMPORTED",
        "reconcile_not_applied": final_reconcile is not None and final_reconcile.status == "NOT_APPLIED" and final_reconcile.side_effect_state == "NOT_APPLIED",
        "operation_not_applied": operation is not None and operation.status == "NOT_APPLIED",
        "source_archived": (queue_dir / "archive" / prepared.execution_attempt_id).is_dir(),
        "reconcile_archived": (queue_dir / "archive" / reconcile_id).is_dir(),
        "no_active_queue_files": not active_files,
    }
    return {
        "schema_version": "shadowbot-filequeue-recovery-acceptance-1.0",
        "ok": all(checks.values()),
        "checks": checks,
        "source_execution_attempt_id": prepared.execution_attempt_id,
        "reconcile_execution_attempt_id": reconcile_id,
        "watchdog_events": watchdog_events,
        "import_events": import_events,
        "reconcile_import_events": reconcile_import_events,
        "active_files": active_files,
    }


def _require_clean_target(runtime_db: Path, queue_dir: Path) -> None:
    if runtime_db.exists():
        raise RuntimeError(f"runtime DB already exists: {runtime_db}")
    if queue_dir.exists() and any(queue_dir.iterdir()):
        raise RuntimeError(f"queue directory must be absent or empty: {queue_dir}")
    queue_dir.mkdir(parents=True, exist_ok=True)


def _claim(queue_dir: Path, execution_attempt_id: str) -> tuple[dict[str, object], str]:
    inbox = queue_dir / "inbox" / f"{execution_attempt_id}.ready.json"
    inbox_checksum = inbox.with_suffix(inbox.suffix + ".sha256")
    working = queue_dir / "working" / f"{execution_attempt_id}.request.json"
    working_checksum = working.with_suffix(working.suffix + ".sha256")
    working.parent.mkdir(parents=True, exist_ok=True)
    os.replace(inbox, working)
    os.replace(inbox_checksum, working_checksum)
    content = working.read_bytes()
    return json.loads(content.decode("utf-8-sig")), hashlib.sha256(content).hexdigest()


def _publish_result(
    queue_dir: Path,
    request: dict[str, object],
    request_hash: str,
    fields: dict[str, object],
) -> None:
    result = {
        "schema_version": "shadowbot-result-1.0",
        "task_id": request["task_id"],
        "operation_id": request["operation_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "execution_mode": request["execution_mode"],
        "instruction_hash": request["instruction_hash"],
        "request_file_sha256": request_hash,
        "worker_id": "SYNTHETIC-RECONCILE-WORKER",
        **fields,
    }
    result_path = queue_dir / "results" / f"{request['execution_attempt_id']}.result.json"
    content = _json_bytes(result)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_bytes(content)
    result_path.with_suffix(result_path.suffix + ".sha256").write_text(
        hashlib.sha256(content).hexdigest() + "\n",
        encoding="ascii",
    )


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(data))


def _json_bytes(data: dict[str, object]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

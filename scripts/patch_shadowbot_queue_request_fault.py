from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_commit_batch import (
    SCHEMA_VERSION,
    compute_instruction_hash,
    validate_request,
)


ALLOWED_FAULTS = {
    "AFTER_SUBMIT_CLICK_UNKNOWN",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Patch a queued ShadowBot request with an explicit test-only fault injection."
    )
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--execution-attempt-id", required=True)
    parser.add_argument("--fault-injection", choices=sorted(ALLOWED_FAULTS), required=True)
    parser.add_argument(
        "--runtime-db",
        type=Path,
        required=True,
        help="Runtime DB whose queued v4 batch and item attempts must be updated atomically.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    request_path = args.queue_dir / "inbox" / f"{args.execution_attempt_id}.ready.json"
    _require_stopped_worker(args.queue_dir)
    if not request_path.exists():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_code": "REQUEST_NOT_IN_INBOX",
                    "request_path": str(request_path),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1

    original_content = request_path.read_bytes()
    payload = json.loads(original_content.decode("utf-8-sig"))
    if str(payload.get("execution_mode") or "") != "COMMIT":
        raise SystemExit("fault injection patch is only allowed for COMMIT requests")
    if payload.get("contract_version") != 4 or payload.get("schema_version") != SCHEMA_VERSION:
        raise SystemExit("fault injection patch requires the current v4 COMMIT contract")
    if str(payload.get("execution_profile") or "").strip().lower() != "development":
        raise SystemExit("fault injection patch is forbidden for production requests")
    if str(payload.get("fault_injection") or "").strip():
        raise SystemExit("request already has fault_injection")
    payload["fault_injection"] = args.fault_injection
    payload["instruction_hash"] = compute_instruction_hash(payload)
    validate_request(payload)
    content = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n"
    ).encode("utf-8")
    checksum_path = request_path.with_suffix(request_path.suffix + ".sha256")
    original_checksum = checksum_path.read_bytes()
    request_file_sha256 = hashlib.sha256(content).hexdigest()
    repository = SQLiteRuntimeRepository(args.runtime_db)
    with repository.connect_read() as connection:
        batch = connection.execute(
            """
            SELECT batch_id, status
            FROM shadowbot_commit_batches
            WHERE batch_id = ? AND execution_attempt_id = ?
            """,
            (payload["batch_id"], args.execution_attempt_id),
        ).fetchone()
        item_attempts = connection.execute(
            """
            SELECT a.execution_attempt_id, a.status
            FROM shadowbot_commit_batch_items AS i
            JOIN shadowbot_execution_attempts AS a
              ON a.execution_attempt_id = i.item_execution_attempt_id
            WHERE i.batch_id = ?
            """,
            (payload["batch_id"],),
        ).fetchall()
    if batch is None or str(batch["status"]) != "QUEUED":
        raise SystemExit("runtime DB batch is not a queued v4 COMMIT; refusing patch")
    if len(item_attempts) != len(payload["items"]) or any(
        str(row["status"]) != "RUNNING" for row in item_attempts
    ):
        raise SystemExit("runtime DB item attempts are not all RUNNING; refusing patch")

    _atomic_write(request_path, content)
    _atomic_write(checksum_path, (request_file_sha256 + "\n").encode("ascii"))
    try:
        with repository.connect_write() as connection, connection:
            now = datetime.now(timezone.utc).isoformat()
            batch_cursor = connection.execute(
                """
                UPDATE shadowbot_commit_batches
                SET instruction_hash = ?, updated_at = ?
                WHERE batch_id = ? AND execution_attempt_id = ? AND status = 'QUEUED'
                """,
                (
                    payload["instruction_hash"],
                    now,
                    payload["batch_id"],
                    args.execution_attempt_id,
                ),
            )
            attempts_cursor = connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET instruction_hash = ?, request_file_sha256 = ?
                WHERE execution_attempt_id IN (
                    SELECT item_execution_attempt_id
                    FROM shadowbot_commit_batch_items
                    WHERE batch_id = ?
                )
                  AND status = 'RUNNING'
                """,
                (
                    payload["instruction_hash"],
                    request_file_sha256,
                    payload["batch_id"],
                ),
            )
            if batch_cursor.rowcount != 1 or attempts_cursor.rowcount != len(payload["items"]):
                raise RuntimeError("runtime DB binding update count mismatch")
    except Exception:
        _atomic_write(request_path, original_content)
        _atomic_write(checksum_path, original_checksum)
        raise
    print(
        json.dumps(
            {
                "ok": True,
                "execution_attempt_id": args.execution_attempt_id,
                "fault_injection": args.fault_injection,
                "request_path": str(request_path),
                "request_file_sha256": request_file_sha256,
                "instruction_hash": payload["instruction_hash"],
                "runtime_db_updated": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _require_stopped_worker(queue_dir: Path) -> None:
    heartbeat_path = queue_dir / "heartbeat.json"
    try:
        heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit("worker heartbeat is unavailable; refusing race-prone patch") from exc
    if str(heartbeat.get("status") or "").strip().upper() != "STOPPED":
        raise SystemExit("worker must be STOPPED before patching a queued request")


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp-" + uuid4().hex)
    try:
        with temporary.open("xb") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    raise SystemExit(main())

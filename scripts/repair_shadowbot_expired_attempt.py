from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.exceptions import ValidationError
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_executor import ShadowBotFileQueueRunner
from app.services.shadowbot_queue import ShadowBotResultImporter
from shadowbot.test2.shadowbot_queue_worker import QueueWorker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair a pre-fix quarantined expired ShadowBot attempt")
    parser.add_argument("--runtime-db", type=Path, required=True)
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--execution-attempt-id", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        event = repair_expired_attempt(args.runtime_db, args.queue_dir, args.execution_attempt_id)
    except (ValidationError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error_code": "EXPIRED_ATTEMPT_REPAIR_REJECTED", "error_message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **event}, ensure_ascii=False, sort_keys=True))
    return 0


def repair_expired_attempt(runtime_db: Path, queue_dir: Path, execution_attempt_id: str) -> dict[str, object]:
    repository = SQLiteRuntimeRepository(runtime_db)
    repository.init_schema()
    attempt = repository.get_shadowbot_execution_attempt(execution_attempt_id)
    if attempt is None:
        raise ValidationError("execution attempt does not exist.")
    if attempt.ended_at is not None:
        raise ValidationError("execution attempt is already finished.")

    matches = sorted((queue_dir / "quarantine").glob(f"*-{execution_attempt_id}.ready.json"))
    if len(matches) != 1:
        raise ValidationError("expected exactly one quarantined request file.")
    request_path = matches[0]
    checksum_path = request_path.with_suffix(request_path.suffix + ".sha256")
    content = request_path.read_bytes()
    request_hash = hashlib.sha256(content).hexdigest()
    if not checksum_path.is_file() or checksum_path.read_text(encoding="ascii").strip().lower() != request_hash:
        raise ValidationError("quarantined request checksum mismatch.")
    if request_hash != attempt.request_file_sha256:
        raise ValidationError("quarantined request hash does not match the database attempt.")
    request = json.loads(content.decode("utf-8-sig"))
    if str(request.get("execution_attempt_id") or "") != execution_attempt_id:
        raise ValidationError("quarantined request attempt ID mismatch.")
    expires_at = datetime.fromisoformat(str(request.get("expires_at") or ""))
    if expires_at.tzinfo is None or expires_at.astimezone(UTC) > datetime.now(UTC):
        raise ValidationError("request is not expired.")

    worker = QueueWorker(
        {
            "queue_dir": str(queue_dir),
            "poll_seconds": 3,
            "max_hours": 1,
            "max_tasks": 1,
            "heartbeat_seconds": 5,
        }
    )
    worker._write_rejected_request_result(
        request,
        request_hash,
        request_path,
        checksum_path,
        error_code="REQUEST_EXPIRED",
        error_message="request expired before Worker claim",
    )
    result_path = queue_dir / "results" / f"{execution_attempt_id}.result.json"
    event = ShadowBotResultImporter(
        repository,
        ShadowBotFileQueueRunner(queue_dir),
        queue_dir,
    ).import_one(result_path)
    repaired = repository.get_shadowbot_execution_attempt(execution_attempt_id)
    return {
        "execution_attempt_id": execution_attempt_id,
        "status": repaired.status if repaired is not None else "",
        "side_effect_state": repaired.side_effect_state if repaired is not None else "",
        "archive_dir": event["archive_dir"],
    }


if __name__ == "__main__":
    raise SystemExit(main())

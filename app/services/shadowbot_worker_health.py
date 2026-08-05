from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def build_shadowbot_worker_health_report(
    queue_dir: Path,
    *,
    expected_status: str = "RUNNING",
    max_age_seconds: float = 15.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read the canonical queue heartbeat without changing Worker state."""

    heartbeat_path = Path(queue_dir) / "heartbeat.json"
    if not heartbeat_path.is_file():
        return {
            "ok": False,
            "error_code": "WORKER_HEARTBEAT_MISSING",
            "heartbeat_path": str(heartbeat_path),
        }
    heartbeat = read_json_object_with_retry(heartbeat_path)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    updated_at = parse_heartbeat_timestamp(heartbeat.get("updated_at"))
    age_seconds = (
        (current - updated_at).total_seconds() if updated_at is not None else None
    )
    status = str(heartbeat.get("status") or "")
    consecutive_failures = int(heartbeat.get("heartbeat_consecutive_failures") or 0)
    total_failures = int(heartbeat.get("heartbeat_write_failures") or 0)
    thread_restarts = int(heartbeat.get("heartbeat_thread_restarts") or 0)
    temporary_files = sorted(
        str(path) for path in queue_dir.glob("heartbeat.json.tmp-*")
    )
    checks = {
        "status_matches": status == expected_status,
        "timestamp_valid": updated_at is not None,
        "heartbeat_fresh": (
            age_seconds is not None
            and -5.0 <= age_seconds <= max(float(max_age_seconds), 0.1)
        ),
        "no_consecutive_write_failures": consecutive_failures == 0,
        "no_orphan_temporary_files": not temporary_files,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "heartbeat_path": str(heartbeat_path),
        "worker_id": str(heartbeat.get("worker_id") or ""),
        "status": status,
        "expected_status": expected_status,
        "updated_at": str(heartbeat.get("updated_at") or ""),
        "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        "processed_count": int(heartbeat.get("processed") or 0),
        "heartbeat_write_failures": total_failures,
        "heartbeat_consecutive_failures": consecutive_failures,
        "heartbeat_last_error": str(heartbeat.get("heartbeat_last_error") or ""),
        "heartbeat_last_error_at": str(heartbeat.get("heartbeat_last_error_at") or ""),
        "heartbeat_thread_restarts": thread_restarts,
        "orphan_temporary_files": temporary_files,
    }


def read_json_object_with_retry(path: Path, attempts: int = 5) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(max(attempts, 1)):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("JSON must contain an object")
            return data
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.05)
    raise RuntimeError(f"cannot read JSON object: {last_error}")


def parse_heartbeat_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)

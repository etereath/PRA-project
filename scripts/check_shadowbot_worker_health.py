from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check ShadowBot Worker heartbeat health")
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--expected-status", choices=("RUNNING", "STOPPED"), default="RUNNING")
    parser.add_argument("--max-age-seconds", type=float, default=15.0)
    parser.add_argument("--strict", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = build_health_report(
        args.queue_dir,
        expected_status=args.expected_status,
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] or not args.strict else 1


def build_health_report(
    queue_dir: Path,
    *,
    expected_status: str = "RUNNING",
    max_age_seconds: float = 15.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    heartbeat_path = queue_dir / "heartbeat.json"
    if not heartbeat_path.is_file():
        return {
            "ok": False,
            "error_code": "WORKER_HEARTBEAT_MISSING",
            "heartbeat_path": str(heartbeat_path),
        }
    heartbeat = _read_json_with_retry(heartbeat_path)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    updated_at = _parse_timestamp(heartbeat.get("updated_at"))
    age_seconds = (current - updated_at).total_seconds() if updated_at is not None else None
    status = str(heartbeat.get("status") or "")
    consecutive_failures = int(heartbeat.get("heartbeat_consecutive_failures") or 0)
    total_failures = int(heartbeat.get("heartbeat_write_failures") or 0)
    thread_restarts = int(heartbeat.get("heartbeat_thread_restarts") or 0)
    temporary_files = sorted(str(path) for path in queue_dir.glob("heartbeat.json.tmp-*"))
    checks = {
        "status_matches": status == expected_status,
        "timestamp_valid": updated_at is not None,
        "heartbeat_fresh": age_seconds is not None and age_seconds <= max(float(max_age_seconds), 0.1),
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
        "heartbeat_write_failures": total_failures,
        "heartbeat_consecutive_failures": consecutive_failures,
        "heartbeat_last_error": str(heartbeat.get("heartbeat_last_error") or ""),
        "heartbeat_last_error_at": str(heartbeat.get("heartbeat_last_error_at") or ""),
        "heartbeat_thread_restarts": thread_restarts,
        "orphan_temporary_files": temporary_files,
    }


def _read_json_with_retry(path: Path, attempts: int = 5) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(max(attempts, 1)):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("heartbeat JSON must contain an object")
            return data
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.05)
    raise RuntimeError(f"cannot read heartbeat JSON: {last_error}")


def _parse_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())

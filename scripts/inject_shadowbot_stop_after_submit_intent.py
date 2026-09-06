from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


POST_INTENT_PHASES = {
    "SUBMIT_INTENT_RECORDED",
    "SUBMIT_CLICKED",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inject stop.signal after a ShadowBot attempt reaches submit intent"
    )
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--execution-attempt-id", required=True)
    parser.add_argument("--log-path", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--poll-seconds", type=float, default=0.05)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = inject_after_submit_intent(
        queue_dir=args.queue_dir,
        execution_attempt_id=args.execution_attempt_id,
        log_path=args.log_path,
        timeout_seconds=max(args.timeout_seconds, 1),
        poll_seconds=max(args.poll_seconds, 0.005),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "INJECTED" else 1


def inject_after_submit_intent(
    *,
    queue_dir: Path,
    execution_attempt_id: str,
    log_path: Path,
    timeout_seconds: float,
    poll_seconds: float,
) -> dict[str, object]:
    working_dir = queue_dir / "working"
    results_dir = queue_dir / "results"
    control_dir = queue_dir / "control"
    phase_path = working_dir / f"{execution_attempt_id}.phase.json"
    result_path = results_dir / f"{execution_attempt_id}.result.json"
    stop_path = control_dir / "stop.signal"
    deadline = time.monotonic() + timeout_seconds
    last_phase = ""
    last_phase_payload: dict[str, object] = {}

    while time.monotonic() < deadline:
        phase_payload = _read_json_if_available(phase_path)
        if phase_payload:
            last_phase_payload = phase_payload
            last_phase = str(phase_payload.get("phase") or "")
            if last_phase in POST_INTENT_PHASES:
                control_dir.mkdir(parents=True, exist_ok=True)
                _atomic_write(stop_path, b"stop\n")
                injected_at = _now_iso()
                result = {
                    "schema_version": "shadowbot-stop-injection-1.0",
                    "status": "INJECTED",
                    "execution_attempt_id": execution_attempt_id,
                    "observed_phase": last_phase,
                    "observed_side_effect_state": str(
                        phase_payload.get("side_effect_state") or ""
                    ),
                    "phase_updated_at": str(phase_payload.get("updated_at") or ""),
                    "signal_written_at": injected_at,
                    "stop_signal_path": str(stop_path),
                }
                _write_json(log_path, result)
                return result
        if result_path.exists():
            result = {
                "schema_version": "shadowbot-stop-injection-1.0",
                "status": "MISSED_RESULT_ALREADY_WRITTEN",
                "execution_attempt_id": execution_attempt_id,
                "last_phase": last_phase,
                "last_phase_payload": last_phase_payload,
                "ended_at": _now_iso(),
            }
            _write_json(log_path, result)
            return result
        time.sleep(poll_seconds)

    result = {
        "schema_version": "shadowbot-stop-injection-1.0",
        "status": "TIMED_OUT",
        "execution_attempt_id": execution_attempt_id,
        "last_phase": last_phase,
        "last_phase_payload": last_phase_payload,
        "ended_at": _now_iso(),
    }
    _write_json(log_path, result)
    return result


def _read_json_if_available(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid4().hex)
    try:
        with temporary.open("xb") as file_obj:
            file_obj.write(data)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_write(path, data)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())

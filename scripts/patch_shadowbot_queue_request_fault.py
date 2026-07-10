from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path


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
        default=None,
        help="Optional runtime DB to update with the patched request_file_sha256 for this test-only request.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    request_path = args.queue_dir / "inbox" / f"{args.execution_attempt_id}.ready.json"
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

    payload = json.loads(request_path.read_text(encoding="utf-8-sig"))
    if str(payload.get("execution_mode") or "") != "COMMIT":
        raise SystemExit("fault injection patch is only allowed for COMMIT requests")
    if str(payload.get("fault_injection") or "").strip():
        raise SystemExit("request already has fault_injection")
    payload["fault_injection"] = args.fault_injection
    content = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n"
    ).encode("utf-8")
    request_path.write_bytes(content)
    checksum_path = request_path.with_suffix(request_path.suffix + ".sha256")
    request_file_sha256 = hashlib.sha256(content).hexdigest()
    checksum_path.write_text(request_file_sha256 + "\n", encoding="ascii")
    database_updated = False
    if args.runtime_db is not None:
        with sqlite3.connect(args.runtime_db) as connection:
            cursor = connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET request_file_sha256 = ?
                WHERE execution_attempt_id = ?
                  AND status = 'RUNNING'
                """,
                (request_file_sha256, args.execution_attempt_id),
            )
            database_updated = cursor.rowcount == 1
        if not database_updated:
            raise SystemExit("runtime DB attempt was not updated; refusing partial patch")
    print(
        json.dumps(
            {
                "ok": True,
                "execution_attempt_id": args.execution_attempt_id,
                "fault_injection": args.fault_injection,
                "request_path": str(request_path),
                "request_file_sha256": request_file_sha256,
                "runtime_db_updated": database_updated,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

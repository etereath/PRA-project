from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.shadowbot_listing_action_contract import (  # noqa: E402
    compute_listing_phase_hash,
    compute_listing_result_hash,
)
from scripts.export_task13_sanitized_evidence import (  # noqa: E402
    _read_json,
    _sanitize,
    _sha256,
    _sha256_json,
)
from scripts.verify_task13_round_trip_evidence import (  # noqa: E402
    validate_round_trip_bundle,
)


DEFAULT_ARCHIVE = Path(r"D:\PRA_Runtime\shadowbot_queue\archive")
DEFAULT_DB = Path("data/runtime/pra_runtime.sqlite3")
DEFAULT_MAPPING = Path("shadowbot/test2/product_identity_mapping.json")
DEFAULT_OUTPUT = Path(
    "docs/evidence/task13/"
    "ROUND-TRIP-AISHA-A-70-Z-20260726"
)
ATTEMPTS = {
    "set_online": "ATTEMPT-T13-AISHA-A-SET-ONLINE-20260726-02",
    "set_offline": "ATTEMPT-T13-AISHA-A-SET-OFFLINE-20260726-04",
    "post_sync": "ATTEMPT-T13-POST-OFFLINE-SYNC-20260726-01",
}


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    )
    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _row(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[Any, ...],
) -> dict[str, Any]:
    value = connection.execute(sql, parameters).fetchone()
    if value is None:
        raise ValueError("database evidence row is missing")
    return dict(value)


def _database_backread(
    runtime_db: Path,
    *,
    requests: dict[str, dict[str, Any]],
    results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    with sqlite3.connect(runtime_db) as connection:
        connection.row_factory = sqlite3.Row
        actions: dict[str, Any] = {}
        for stage in ("set_online", "set_offline"):
            request = requests[stage]
            item = request["items"][0]
            actions[stage] = {
                "batch": _row(
                    connection,
                    "SELECT * FROM shadowbot_listing_action_batches "
                    "WHERE batch_id = ?",
                    (request["batch_id"],),
                ),
                "item": _row(
                    connection,
                    "SELECT * FROM shadowbot_listing_action_batch_items "
                    "WHERE operation_id = ?",
                    (item["operation_id"],),
                ),
                "task": _row(
                    connection,
                    "SELECT task_id, task_status, result_message "
                    "FROM tasks WHERE task_id = ?",
                    (item["source_task_id"],),
                ),
            }
        snapshot = results["post_sync"]["snapshot"]
        sku = str(requests["set_online"]["items"][0]["internal_sku"])
        return {
            "schema_version": "task13-round-trip-database-backread-1.0",
            "internal_sku": sku,
            **actions,
            "current_write_lock": _row(
                connection,
                "SELECT * FROM shadowbot_write_locks "
                "WHERE write_identity_key = ?",
                (
                    requests["set_offline"]["items"][0][
                        "write_identity_key"
                    ],
                ),
            ),
            "post_sync_snapshot": _row(
                connection,
                "SELECT * FROM listing_sync_snapshots "
                "WHERE snapshot_id = ?",
                (snapshot["snapshot_id"],),
            ),
            "post_sync_item": _row(
                connection,
                "SELECT * FROM listing_sync_snapshot_items "
                "WHERE snapshot_id = ? AND internal_sku = ?",
                (snapshot["snapshot_id"], sku),
            ),
            "listing_status": _row(
                connection,
                "SELECT * FROM listing_status WHERE internal_sku = ?",
                (sku,),
            ),
        }


def _receipt(
    runtime_db: Path,
    attempt_id: str,
) -> dict[str, Any]:
    with sqlite3.connect(runtime_db) as connection:
        connection.row_factory = sqlite3.Row
        return _row(
            connection,
            "SELECT * FROM shadowbot_listing_result_receipts "
            "WHERE execution_attempt_id = ?",
            (attempt_id,),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--runtime-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    requests: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    original_hashes: dict[str, dict[str, str]] = {}
    for stage, attempt_id in ATTEMPTS.items():
        source_dir = args.queue_archive / attempt_id
        request_source = source_dir / f"{attempt_id}.request.json"
        result_source = source_dir / f"{attempt_id}.result.json"
        phase_source = source_dir / f"{attempt_id}.phase.json"
        ack_source = source_dir / f"{attempt_id}.import.ack.json"
        report_suffix = (
            "sync-report.md"
            if stage == "post_sync"
            else "listing-action-report.md"
        )
        report_source = source_dir / f"{attempt_id}.{report_suffix}"
        for path in (
            request_source,
            result_source,
            phase_source,
            ack_source,
            report_source,
        ):
            if not path.is_file():
                raise FileNotFoundError(path)

        request = _sanitize(_read_json(request_source))
        request_path = (
            args.output / f"{stage}.request.sanitized.json"
        )
        _atomic_write_json(request_path, request)
        request_sha = "sha256:" + _sha256(request_path)

        result = _sanitize(_read_json(result_source))
        result["request_file_sha256"] = request_sha
        result["result_payload_sha256"] = compute_listing_result_hash(
            result
        )
        result_path = args.output / f"{stage}.result.sanitized.json"
        _atomic_write_json(result_path, result)

        phase = _sanitize(_read_json(phase_source))
        phase["request_file_sha256"] = request_sha
        phase["phase_snapshot_sha256"] = compute_listing_phase_hash(phase)
        _atomic_write_json(
            args.output / f"{stage}.phase.sanitized.json",
            phase,
        )

        receipt_original = _receipt(args.runtime_db, attempt_id)
        receipt = _sanitize(receipt_original)
        receipt["result_sha256"] = _sha256(result_path)
        _atomic_write_json(
            args.output / f"{stage}.receipt.sanitized.json",
            receipt,
        )

        ack = _sanitize(_read_json(ack_source))
        ack["result_file_sha256"] = _sha256(result_path)
        _atomic_write_json(
            args.output / f"{stage}.ack.sanitized.json",
            ack,
        )
        report = report_source.read_text(encoding="utf-8-sig")
        report = report.replace(
            str(args.queue_archive),
            "<REDACTED_PATH>",
        )
        (
            args.output / f"{stage}.report.sanitized.md"
        ).write_text(report, encoding="utf-8", newline="\n")

        requests[stage] = request
        results[stage] = result
        original_hashes[stage] = {
            "request": _sha256(request_source),
            "result": _sha256(result_source),
            "phase": _sha256(phase_source),
            "ack": _sha256(ack_source),
            "report": _sha256(report_source),
            "receipt_canonical_json": _sha256_json(receipt_original),
        }

    mapping_bytes = args.mapping.read_bytes()
    mapping_bytes.decode("utf-8-sig")
    (args.output / "product_identity_mapping.json").write_bytes(
        mapping_bytes
    )
    database = _sanitize(
        _database_backread(
            args.runtime_db,
            requests=requests,
            results=results,
        )
    )
    _atomic_write_json(
        args.output / "database_backread.sanitized.json",
        database,
    )

    online_item = results["set_online"]["items"][0]
    offline_item = results["set_offline"]["items"][0]
    snapshot = results["post_sync"]["snapshot"]
    manifest = {
        "schema_version": "task13-round-trip-evidence-manifest-1.0",
        "expected": {
            "internal_sku": online_item["internal_sku"],
            "online_operation_id": online_item["operation_id"],
            "offline_operation_id": offline_item["operation_id"],
            "post_sync_snapshot_id": snapshot["snapshot_id"],
            "final_listing_location": "waiting_only",
            "final_online_status": "offline",
        },
        "attempts": ATTEMPTS,
        "original_archive_sha256": original_hashes,
        "mapping_sha256": _sha256(args.mapping),
    }
    _atomic_write_json(args.output / "evidence_manifest.json", manifest)

    validation = validate_round_trip_bundle(args.output)
    validation.update(
        {
            "schema_version": (
                "task13-round-trip-evidence-validation-1.0"
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "redaction_policy": {
                "replaced": [
                    "local_or_unc_paths",
                    "worker_device_identifier",
                ],
                "preserved": [
                    "business_identity",
                    "prices_and_inventory",
                    "operation_and_attempt_ids",
                    "timestamps",
                    "database_ledger_and_snapshot_facts",
                ],
            },
        }
    )
    _atomic_write_json(
        args.output / "validation_report.json",
        validation,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "output_dir": str(args.output),
                "internal_sku": validation["internal_sku"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

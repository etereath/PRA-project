from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
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
    _write_index,
)
from scripts.export_task13_unknown_reconcile_evidence import (  # noqa: E402
    _atomic_write_json,
    _receipt,
    _row,
)
from scripts.verify_task13_already_applied_evidence import (  # noqa: E402
    validate_already_applied_bundle,
)


DEFAULT_ARCHIVE = Path(r"D:\PRA_Runtime\shadowbot_queue\archive")
DEFAULT_DB = Path("data/runtime/pra_runtime.sqlite3")
DEFAULT_MAPPING = Path("shadowbot/test2/product_identity_mapping.json")
DEFAULT_OUTPUT = Path(
    "docs/evidence/task13/"
    "ALREADY-APPLIED-AISHA-B-60-Z-20260727"
)
ATTEMPT_ID = "ATTEMPT-f2a0b9089ed84b7b"


def _database_backread(
    runtime_db: Path,
    *,
    request: dict[str, Any],
) -> dict[str, Any]:
    item = request["items"][0]
    operation_id = str(item["operation_id"])
    source_task_id = str(item["source_task_id"])
    with sqlite3.connect(runtime_db) as connection:
        connection.row_factory = sqlite3.Row
        open_review_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM review_tasks "
                "WHERE source_task_id = ? "
                "AND review_status NOT IN ('cancelled', 'completed')",
                (source_task_id,),
            ).fetchone()[0]
        )
        return {
            "schema_version": (
                "task13-already-applied-database-backread-1.0"
            ),
            "internal_sku": item["internal_sku"],
            "task": _row(
                connection,
                "SELECT * FROM tasks WHERE task_id = ?",
                (source_task_id,),
            ),
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
                (operation_id,),
            ),
            "operation": _row(
                connection,
                "SELECT * FROM shadowbot_operations "
                "WHERE operation_id = ?",
                (operation_id,),
            ),
            "execution_attempt": _row(
                connection,
                "SELECT * FROM shadowbot_execution_attempts "
                "WHERE operation_id = ?",
                (operation_id,),
            ),
            "write_lock": _row(
                connection,
                "SELECT * FROM shadowbot_write_locks "
                "WHERE operation_id = ?",
                (operation_id,),
            ),
            "open_review_count": open_review_count,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--runtime-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    source_dir = args.queue_archive / ATTEMPT_ID
    request_source = source_dir / f"{ATTEMPT_ID}.request.json"
    result_source = source_dir / f"{ATTEMPT_ID}.result.json"
    phase_source = source_dir / f"{ATTEMPT_ID}.phase.json"
    ack_source = source_dir / f"{ATTEMPT_ID}.import.ack.json"
    report_source = (
        source_dir / f"{ATTEMPT_ID}.listing-action-report.md"
    )
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
    request_path = args.output / "commit.request.sanitized.json"
    _atomic_write_json(request_path, request)
    request_sha = "sha256:" + _sha256(request_path)

    result = _sanitize(_read_json(result_source))
    result["request_file_sha256"] = request_sha
    result["result_payload_sha256"] = compute_listing_result_hash(result)
    result_path = args.output / "commit.result.sanitized.json"
    _atomic_write_json(result_path, result)

    phase = _sanitize(_read_json(phase_source))
    phase["request_file_sha256"] = request_sha
    phase["phase_snapshot_sha256"] = compute_listing_phase_hash(phase)
    _atomic_write_json(
        args.output / "commit.phase.sanitized.json",
        phase,
    )

    receipt_original = _receipt(args.runtime_db, ATTEMPT_ID)
    receipt = _sanitize(receipt_original)
    receipt["result_sha256"] = _sha256(result_path)
    _atomic_write_json(
        args.output / "commit.receipt.sanitized.json",
        receipt,
    )

    ack = _sanitize(_read_json(ack_source))
    ack["result_file_sha256"] = _sha256(result_path)
    _atomic_write_json(
        args.output / "commit.ack.sanitized.json",
        ack,
    )

    report = report_source.read_text(encoding="utf-8-sig")
    report = report.replace(str(args.queue_archive), "<REDACTED_PATH>")
    (args.output / "commit.report.sanitized.md").write_text(
        report,
        encoding="utf-8",
        newline="\n",
    )

    mapping_bytes = args.mapping.read_bytes()
    mapping_bytes.decode("utf-8-sig")
    (args.output / "product_identity_mapping.json").write_bytes(
        mapping_bytes
    )
    database = _sanitize(
        _database_backread(args.runtime_db, request=request)
    )
    _atomic_write_json(
        args.output / "database_backread.sanitized.json",
        database,
    )

    request_item = request["items"][0]
    manifest = {
        "schema_version": (
            "task13-already-applied-evidence-manifest-1.0"
        ),
        "expected": {
            "internal_sku": request_item["internal_sku"],
            "batch_id": request["batch_id"],
            "operation_id": request_item["operation_id"],
            "execution_attempt_id": request["execution_attempt_id"],
            "item_execution_attempt_id": request_item[
                "item_execution_attempt_id"
            ],
            "result_id": result["result_id"],
            "operation_result": "ALREADY_APPLIED",
            "detail_save_clicked": False,
            "action_confirm_clicked": False,
            "final_task_status": "success",
            "final_write_lock_status": "RELEASED",
        },
        "original_archive_sha256": {
            "request": _sha256(request_source),
            "result": _sha256(result_source),
            "phase": _sha256(phase_source),
            "ack": _sha256(ack_source),
            "report": _sha256(report_source),
            "receipt_canonical_json": _sha256_json(receipt_original),
        },
        "mapping_sha256": _sha256(args.mapping),
    }
    _atomic_write_json(args.output / "evidence_manifest.json", manifest)

    validation = validate_already_applied_bundle(args.output)
    validation.update(
        {
            "schema_version": (
                "task13-already-applied-evidence-validation-1.0"
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "redaction_policy": {
                "replaced": [
                    "local_or_unc_paths",
                    "worker_device_identifier",
                ],
                "preserved": [
                    "business_identity",
                    "operation_and_attempt_ids",
                    "timestamps",
                    "zero_click_facts",
                    "database_ledger_facts",
                ],
            },
        }
    )
    _atomic_write_json(
        args.output / "validation_report.json",
        validation,
    )
    _write_index(args.output.parent, validation)
    print(
        json.dumps(
            {
                "ok": True,
                "output_dir": str(args.output),
                "internal_sku": validation["internal_sku"],
                "batch_id": validation["batch_id"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

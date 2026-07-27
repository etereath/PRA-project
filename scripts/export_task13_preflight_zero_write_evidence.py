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
from scripts.verify_task13_preflight_zero_write_evidence import (  # noqa: E402
    validate_preflight_zero_write_bundle,
)


DEFAULT_ARCHIVE = Path(r"D:\PRA_Runtime\shadowbot_queue\archive")
DEFAULT_DB = Path("data/runtime/pra_runtime.sqlite3")
DEFAULT_MAPPING = Path("shadowbot/test2/product_identity_mapping.json")
DEFAULT_OUTPUT = Path(
    "docs/evidence/task13/"
    "PREFLIGHT-ZERO-WRITE-AISHA-A-E-20260727"
)
ATTEMPT_ID = "ATTEMPT-591f3a642e2b43f9"


def _rows(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[Any, ...],
) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, parameters)]


def _database_backread(
    runtime_db: Path,
    *,
    request: dict[str, Any],
) -> dict[str, Any]:
    operation_ids = tuple(str(item["operation_id"]) for item in request["items"])
    source_task_ids = tuple(str(item["source_task_id"]) for item in request["items"])
    placeholders = ",".join("?" for _ in operation_ids)
    task_placeholders = ",".join("?" for _ in source_task_ids)
    with sqlite3.connect(runtime_db) as connection:
        connection.row_factory = sqlite3.Row
        open_review_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM review_tasks "
                f"WHERE source_task_id IN ({task_placeholders}) "
                "AND review_status NOT IN ('cancelled', 'completed')",
                source_task_ids,
            ).fetchone()[0]
        )
        return {
            "schema_version": (
                "task13-preflight-zero-write-database-backread-1.0"
            ),
            "batch": _row(
                connection,
                "SELECT * FROM shadowbot_listing_action_batches "
                "WHERE batch_id = ?",
                (request["batch_id"],),
            ),
            "batch_items": _rows(
                connection,
                "SELECT * FROM shadowbot_listing_action_batch_items "
                "WHERE batch_id = ? ORDER BY item_id",
                (request["batch_id"],),
            ),
            "tasks": _rows(
                connection,
                f"SELECT * FROM tasks WHERE task_id IN ({task_placeholders}) "
                "ORDER BY task_id",
                source_task_ids,
            ),
            "operations": _rows(
                connection,
                "SELECT * FROM shadowbot_operations "
                f"WHERE operation_id IN ({placeholders}) ORDER BY operation_id",
                operation_ids,
            ),
            "execution_attempts": _rows(
                connection,
                "SELECT * FROM shadowbot_execution_attempts "
                f"WHERE operation_id IN ({placeholders}) ORDER BY operation_id",
                operation_ids,
            ),
            "write_locks": _rows(
                connection,
                "SELECT * FROM shadowbot_write_locks "
                "WHERE batch_id = ? ORDER BY operation_id",
                (request["batch_id"],),
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
    report_source = source_dir / f"{ATTEMPT_ID}.listing-action-report.md"
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
    _atomic_write_json(args.output / "commit.phase.sanitized.json", phase)

    receipt_original = _receipt(args.runtime_db, ATTEMPT_ID)
    receipt = _sanitize(receipt_original)
    receipt["result_sha256"] = _sha256(result_path)
    _atomic_write_json(
        args.output / "commit.receipt.sanitized.json",
        receipt,
    )

    ack = _sanitize(_read_json(ack_source))
    ack["result_file_sha256"] = _sha256(result_path)
    _atomic_write_json(args.output / "commit.ack.sanitized.json", ack)

    report = report_source.read_text(encoding="utf-8-sig")
    report = report.replace(str(args.queue_archive), "<REDACTED_PATH>")
    (args.output / "commit.report.sanitized.md").write_text(
        report,
        encoding="utf-8",
        newline="\n",
    )

    mapping_bytes = args.mapping.read_bytes()
    mapping_bytes.decode("utf-8-sig")
    (args.output / "product_identity_mapping.json").write_bytes(mapping_bytes)

    database = _sanitize(
        _database_backread(args.runtime_db, request=request)
    )
    _atomic_write_json(
        args.output / "database_backread.sanitized.json",
        database,
    )

    request_items = {
        str(item["internal_sku"]): item for item in request["items"]
    }
    manifest = {
        "schema_version": (
            "task13-preflight-zero-write-evidence-manifest-1.0"
        ),
        "expected": {
            "batch_id": request["batch_id"],
            "execution_attempt_id": request["execution_attempt_id"],
            "result_id": result["result_id"],
            "normal_internal_sku": "AISHA-A-70-Z",
            "mismatch_internal_sku": "AISHA-E-45-Z",
            "normal_operation_id": request_items["AISHA-A-70-Z"][
                "operation_id"
            ],
            "mismatch_operation_id": request_items["AISHA-E-45-Z"][
                "operation_id"
            ],
            "batch_status": "FAILED",
            "error_code": "LISTING_DATA_MISMATCH",
            "write_click_count": 0,
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

    validation = validate_preflight_zero_write_bundle(args.output)
    validation.update(
        {
            "schema_version": (
                "task13-preflight-zero-write-evidence-validation-1.0"
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
                    "preflight_observations",
                    "zero_click_facts",
                    "database_ledger_facts",
                ],
            },
        }
    )
    _atomic_write_json(args.output / "validation_report.json", validation)
    _write_index(args.output.parent, validation)
    print(
        json.dumps(
            {
                "ok": True,
                "output_dir": str(args.output),
                "batch_id": validation["batch_id"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

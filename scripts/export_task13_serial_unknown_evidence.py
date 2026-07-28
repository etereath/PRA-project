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
from scripts.verify_task13_serial_unknown_evidence import (  # noqa: E402
    validate_serial_unknown_bundle,
)


DEFAULT_ARCHIVE = Path(r"D:\PRA_Runtime\shadowbot_queue\archive")
DEFAULT_DB = Path("data/runtime/pra_runtime.sqlite3")
DEFAULT_MAPPING = Path("shadowbot/test2/product_identity_mapping.json")
DEFAULT_OUTPUT = Path(
    "docs/evidence/task13/SERIAL-UNKNOWN-AISHA-B-C-D-20260726"
)
ATTEMPTS = {
    "commit": "ATTEMPT-T13-CONTROLLED-UNKNOWN-20260726-01",
    "reconcile": "RECONCILE-e88fb8a4b4d60936236f0e0a",
}


def _rows(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[Any, ...],
) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, parameters)]


def _result_source(source_dir: Path, attempt_id: str) -> Path:
    exact = source_dir / f"{attempt_id}.result.json"
    if exact.is_file():
        return exact
    candidates = sorted(source_dir.glob(f"*{attempt_id}.result.json"))
    if len(candidates) != 1:
        raise FileNotFoundError(
            f"expected one result for {attempt_id}, found {len(candidates)}"
        )
    return candidates[0]


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
        sources = {
            "request": source_dir / f"{attempt_id}.request.json",
            "result": _result_source(source_dir, attempt_id),
            "phase": source_dir / f"{attempt_id}.phase.json",
            "ack": source_dir / f"{attempt_id}.import.ack.json",
            "report": source_dir / f"{attempt_id}.listing-action-report.md",
        }
        for path in sources.values():
            if not path.is_file():
                raise FileNotFoundError(path)

        request = _sanitize(_read_json(sources["request"]))
        request_path = args.output / f"{stage}.request.sanitized.json"
        _atomic_write_json(request_path, request)
        request_sha = "sha256:" + _sha256(request_path)

        result = _sanitize(_read_json(sources["result"]))
        result["request_file_sha256"] = request_sha
        result["result_payload_sha256"] = compute_listing_result_hash(result)
        result_path = args.output / f"{stage}.result.sanitized.json"
        _atomic_write_json(result_path, result)

        phase = _sanitize(_read_json(sources["phase"]))
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

        ack = _sanitize(_read_json(sources["ack"]))
        ack["result_file_sha256"] = _sha256(result_path)
        _atomic_write_json(
            args.output / f"{stage}.ack.sanitized.json",
            ack,
        )

        report = sources["report"].read_text(encoding="utf-8-sig")
        report = report.replace(str(args.queue_archive), "<REDACTED_PATH>")
        (args.output / f"{stage}.report.sanitized.md").write_text(
            report,
            encoding="utf-8",
            newline="\n",
        )
        requests[stage] = request
        results[stage] = result
        original_hashes[stage] = {
            name: _sha256(path) for name, path in sources.items()
        }
        original_hashes[stage]["result_filename"] = sources["result"].name
        original_hashes[stage]["receipt_canonical_json"] = _sha256_json(
            receipt_original
        )

    commit_request = requests["commit"]
    operation_ids = tuple(
        str(item["operation_id"]) for item in commit_request["items"]
    )
    source_task_ids = tuple(
        str(item["source_task_id"]) for item in commit_request["items"]
    )
    operation_placeholders = ",".join("?" for _ in operation_ids)
    task_placeholders = ",".join("?" for _ in source_task_ids)
    with sqlite3.connect(args.runtime_db) as connection:
        connection.row_factory = sqlite3.Row
        open_review_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM review_tasks "
                f"WHERE source_task_id IN ({task_placeholders}) "
                "AND review_status NOT IN ('cancelled', 'completed')",
                source_task_ids,
            ).fetchone()[0]
        )
        database = {
            "schema_version": (
                "task13-serial-unknown-database-backread-1.0"
            ),
            "batch": _row(
                connection,
                "SELECT * FROM shadowbot_listing_action_batches "
                "WHERE batch_id = ?",
                (commit_request["batch_id"],),
            ),
            "batch_items": _rows(
                connection,
                "SELECT * FROM shadowbot_listing_action_batch_items "
                "WHERE batch_id = ? ORDER BY item_id",
                (commit_request["batch_id"],),
            ),
            "operations": _rows(
                connection,
                "SELECT * FROM shadowbot_operations "
                f"WHERE operation_id IN ({operation_placeholders}) "
                "ORDER BY operation_id",
                operation_ids,
            ),
            "execution_attempts": _rows(
                connection,
                "SELECT * FROM shadowbot_execution_attempts "
                f"WHERE operation_id IN ({operation_placeholders}) "
                "ORDER BY started_at",
                operation_ids,
            ),
            "write_locks": _rows(
                connection,
                "SELECT * FROM shadowbot_write_locks "
                f"WHERE operation_id IN ({operation_placeholders}) "
                "ORDER BY operation_id",
                operation_ids,
            ),
            "open_review_count": open_review_count,
        }
    _atomic_write_json(
        args.output / "database_backread.sanitized.json",
        _sanitize(database),
    )

    mapping_bytes = args.mapping.read_bytes()
    mapping_bytes.decode("utf-8-sig")
    (args.output / "product_identity_mapping.json").write_bytes(mapping_bytes)

    manifest = {
        "schema_version": "task13-serial-unknown-evidence-manifest-1.0",
        "expected": {
            "commit_batch_id": commit_request["batch_id"],
            "commit_execution_attempt_id": commit_request[
                "execution_attempt_id"
            ],
            "reconcile_execution_attempt_id": requests["reconcile"][
                "execution_attempt_id"
            ],
            "verified_internal_sku": "AISHA-C-55-Z",
            "unknown_internal_sku": "AISHA-D-50-Z",
            "not_attempted_internal_sku": "AISHA-B-60-Z",
            "commit_status": "UNKNOWN",
            "reconcile_status": "VERIFIED",
        },
        "original_archive_sha256": original_hashes,
        "mapping_sha256": _sha256(args.mapping),
    }
    _atomic_write_json(args.output / "evidence_manifest.json", manifest)

    validation = validate_serial_unknown_bundle(args.output)
    validation.update(
        {
            "schema_version": (
                "task13-serial-unknown-evidence-validation-1.0"
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
                    "verified_unknown_not_attempted_order",
                    "database_original_attempt_ledger",
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
                "batch_id": commit_request["batch_id"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

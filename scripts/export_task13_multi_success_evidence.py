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
from scripts.verify_task13_multi_success_evidence import (  # noqa: E402
    validate_multi_success_bundle,
)


DEFAULT_ARCHIVE = Path(r"D:\PRA_Runtime\shadowbot_queue\archive")
DEFAULT_DB = Path("data/runtime/pra_runtime.sqlite3")
DEFAULT_MAPPING = Path("shadowbot/test2/product_identity_mapping.json")
DEFAULT_OUTPUT = Path(
    "docs/evidence/task13/MULTI-SUCCESS-AISHA-E-CAPPUCCINO-E-20260726"
)
ATTEMPTS = {
    "set_online": "ATTEMPT-T13-OPTIMIZED-SET-ONLINE-20260726-02",
    "set_offline": "ATTEMPT-T13-OPTIMIZED-SET-OFFLINE-20260726-02",
}


def _rows(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[Any, ...],
) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, parameters)]


def _database_stage(
    connection: sqlite3.Connection,
    *,
    request: dict[str, Any],
) -> dict[str, Any]:
    operation_ids = tuple(str(item["operation_id"]) for item in request["items"])
    placeholders = ",".join("?" for _ in operation_ids)
    return {
        "batch": _row(
            connection,
            "SELECT * FROM shadowbot_listing_action_batches WHERE batch_id = ?",
            (request["batch_id"],),
        ),
        "batch_items": _rows(
            connection,
            "SELECT * FROM shadowbot_listing_action_batch_items "
            "WHERE batch_id = ? ORDER BY item_id",
            (request["batch_id"],),
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
    }


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
            "result": source_dir / f"{attempt_id}.result.json",
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
        original_hashes[stage]["receipt_canonical_json"] = _sha256_json(
            receipt_original
        )

    mapping_bytes = args.mapping.read_bytes()
    mapping_bytes.decode("utf-8-sig")
    (args.output / "product_identity_mapping.json").write_bytes(mapping_bytes)

    with sqlite3.connect(args.runtime_db) as connection:
        connection.row_factory = sqlite3.Row
        database = {
            "schema_version": "task13-multi-success-database-backread-1.0",
            **{
                stage: _database_stage(connection, request=request)
                for stage, request in requests.items()
            },
        }
    _atomic_write_json(
        args.output / "database_backread.sanitized.json",
        _sanitize(database),
    )

    internal_skus = sorted(
        str(item["internal_sku"])
        for item in requests["set_online"]["items"]
    )
    manifest = {
        "schema_version": "task13-multi-success-evidence-manifest-1.0",
        "expected": {
            "internal_skus": internal_skus,
            **{
                f"{stage}_batch_id": requests[stage]["batch_id"]
                for stage in ATTEMPTS
            },
            **{
                f"{stage}_execution_attempt_id": requests[stage][
                    "execution_attempt_id"
                ]
                for stage in ATTEMPTS
            },
            "per_batch_target_count": 2,
            "per_batch_verified_count": 2,
        },
        "original_archive_sha256": original_hashes,
        "mapping_sha256": _sha256(args.mapping),
    }
    _atomic_write_json(args.output / "evidence_manifest.json", manifest)

    validation = validate_multi_success_bundle(args.output)
    validation.update(
        {
            "schema_version": (
                "task13-multi-success-evidence-validation-1.0"
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
                    "per_item_click_and_readback_facts",
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
                "internal_skus": internal_skus,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

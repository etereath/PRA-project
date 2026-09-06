from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
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
    _write_index,
)
from scripts.verify_task13_unknown_reconcile_evidence import (  # noqa: E402
    validate_unknown_reconcile_bundle,
)


DEFAULT_ARCHIVE = Path(r"D:\PRA_Runtime\shadowbot_queue\archive")
DEFAULT_DB = Path("data/runtime/pra_runtime.sqlite3")
DEFAULT_MAPPING = Path("shadowbot/test2/product_identity_mapping.json")
DEFAULT_OUTPUT = Path(
    "docs/evidence/task13/"
    "UNKNOWN-RECONCILE-AISHA-B-60-Z-20260727"
)
ATTEMPTS = {
    "commit": "ATTEMPT-664d3064fd864dcc",
    "reconcile": "RECONCILE-ab020713eb4633c24441f141",
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


def _rows(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[Any, ...],
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(sql, parameters).fetchall()
    ]


def _database_backread(
    runtime_db: Path,
    *,
    commit_request: dict[str, Any],
) -> dict[str, Any]:
    item = commit_request["items"][0]
    operation_id = str(item["operation_id"])
    source_task_id = str(item["source_task_id"])
    with sqlite3.connect(runtime_db) as connection:
        connection.row_factory = sqlite3.Row
        attempts = _rows(
            connection,
            "SELECT * FROM shadowbot_execution_attempts "
            "WHERE operation_id = ? ORDER BY started_at",
            (operation_id,),
        )
        reviews = _rows(
            connection,
            "SELECT * FROM review_tasks WHERE source_task_id = ? "
            "ORDER BY created_at",
            (source_task_id,),
        )
        return {
            "schema_version": (
                "task13-unknown-reconcile-database-backread-1.0"
            ),
            "internal_sku": item["internal_sku"],
            "batch": _row(
                connection,
                "SELECT * FROM shadowbot_listing_action_batches "
                "WHERE batch_id = ?",
                (commit_request["batch_id"],),
            ),
            "batch_registry": _row(
                connection,
                "SELECT * FROM shadowbot_batch_registry WHERE batch_id = ?",
                (commit_request["batch_id"],),
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
            "execution_attempts": attempts,
            "attempt_mode_counts": dict(
                sorted(
                    Counter(
                        str(attempt["execution_mode"])
                        for attempt in attempts
                    ).items()
                )
            ),
            "write_lock": _row(
                connection,
                "SELECT * FROM shadowbot_write_locks "
                "WHERE write_identity_key = ?",
                (item["write_identity_key"],),
            ),
            "task": _row(
                connection,
                "SELECT * FROM tasks WHERE task_id = ?",
                (source_task_id,),
            ),
            "reviews": reviews,
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
    parser.add_argument(
        "--commit-attempt",
        default=ATTEMPTS["commit"],
    )
    parser.add_argument(
        "--reconcile-attempt",
        default=ATTEMPTS["reconcile"],
    )
    parser.add_argument(
        "--expected-final-operation-result",
        choices=("VERIFIED", "NOT_APPLIED"),
        default="VERIFIED",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    attempts = {
        "commit": args.commit_attempt,
        "reconcile": args.reconcile_attempt,
    }
    final_task_status = (
        "success"
        if args.expected_final_operation_result == "VERIFIED"
        else "failed"
    )

    requests: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    original_hashes: dict[str, dict[str, str]] = {}
    for stage, attempt_id in attempts.items():
        source_dir = args.queue_archive / attempt_id
        request_source = source_dir / f"{attempt_id}.request.json"
        result_source = source_dir / f"{attempt_id}.result.json"
        phase_source = source_dir / f"{attempt_id}.phase.json"
        ack_source = source_dir / f"{attempt_id}.import.ack.json"
        report_source = (
            source_dir / f"{attempt_id}.listing-action-report.md"
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
        request_path = args.output / f"{stage}.request.sanitized.json"
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
        report = report.replace(str(args.queue_archive), "<REDACTED_PATH>")
        (args.output / f"{stage}.report.sanitized.md").write_text(
            report,
            encoding="utf-8",
            newline="\n",
        )

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
            commit_request=requests["commit"],
        )
    )
    _atomic_write_json(
        args.output / "database_backread.sanitized.json",
        database,
    )

    commit_item = requests["commit"]["items"][0]
    reconcile_item = requests["reconcile"]["items"][0]
    manifest = {
        "schema_version": (
            "task13-unknown-reconcile-evidence-manifest-1.0"
        ),
        "expected": {
            "internal_sku": commit_item["internal_sku"],
            "batch_id": requests["commit"]["batch_id"],
            "operation_id": commit_item["operation_id"],
            "commit_execution_attempt_id": requests["commit"][
                "execution_attempt_id"
            ],
            "commit_item_execution_attempt_id": commit_item[
                "item_execution_attempt_id"
            ],
            "commit_result_id": results["commit"]["result_id"],
            "reconcile_execution_attempt_id": requests["reconcile"][
                "execution_attempt_id"
            ],
            "reconcile_item_execution_attempt_id": reconcile_item[
                "item_execution_attempt_id"
            ],
            "reconcile_result_id": results["reconcile"]["result_id"],
            "final_operation_result": (
                args.expected_final_operation_result
            ),
            "final_task_status": final_task_status,
            "final_write_lock_status": "RELEASED",
        },
        "attempts": attempts,
        "original_archive_sha256": original_hashes,
        "mapping_sha256": _sha256(args.mapping),
    }
    _atomic_write_json(args.output / "evidence_manifest.json", manifest)

    validation = validate_unknown_reconcile_bundle(
        args.output,
        expected_final_operation_result=(
            args.expected_final_operation_result
        ),
    )
    validation.update(
        {
            "schema_version": (
                "task13-unknown-reconcile-evidence-validation-1.0"
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
                    "prices_and_inventory",
                    "timestamps",
                    "click_boundary",
                    "database_ledger_and_review_facts",
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

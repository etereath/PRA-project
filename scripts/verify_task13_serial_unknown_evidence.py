from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.verify_task13_unknown_reconcile_evidence import (  # noqa: E402
    _assert_sanitized,
    _check,
    _read_json,
    _sha256,
    _validate_count_equation,
    _validate_stage,
)


DEFAULT_BUNDLE = Path(
    "docs/evidence/task13/"
    "SERIAL-UNKNOWN-AISHA-B-C-D-20260726"
)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _by_sku(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["internal_sku"]): row for row in rows}


def validate_serial_unknown_bundle(bundle_dir: Path) -> dict[str, Any]:
    common = {
        "mapping": bundle_dir / "product_identity_mapping.json",
        "database": bundle_dir / "database_backread.sanitized.json",
        "manifest": bundle_dir / "evidence_manifest.json",
    }
    for path in common.values():
        _check(path.is_file(), f"missing evidence file: {path}")
    mapping = _read_json(common["mapping"])
    database = _read_json(common["database"])
    manifest = _read_json(common["manifest"])
    for document in (mapping, database, manifest):
        _assert_sanitized(document)

    commit = _validate_stage(bundle_dir, "commit")
    reconcile = _validate_stage(bundle_dir, "reconcile")
    commit_request = commit["request"]
    commit_result = commit["result"]
    reconcile_request = reconcile["request"]
    reconcile_result = reconcile["result"]
    _validate_count_equation(commit_result, stage="commit")
    _validate_count_equation(reconcile_result, stage="reconcile")

    _check(commit_request["execution_mode"] == "COMMIT", "commit mode mismatch")
    _check(
        commit_request["action_type"] == "set_offline",
        "commit action mismatch",
    )
    _check(len(commit_request["items"]) == 3, "commit is not three-item")
    _check(len(commit_result["items"]) == 3, "result is not three-item")
    _check(commit_result["status"] == "UNKNOWN", "commit is not UNKNOWN")
    _check(
        commit_result["batch_status"] == "UNKNOWN",
        "commit batch is not UNKNOWN",
    )
    _check(
        commit_result["error_code"]
        == "CONTROLLED_AFTER_ACTION_CLICK_UNKNOWN",
        "controlled UNKNOWN error code mismatch",
    )
    counts = commit_result["counts"]
    _check(
        int(counts["batch_target_count"]) == 3
        and int(counts["attempted_count"]) == 2
        and int(counts["verified_count"]) == 1
        and int(counts["unknown_count"]) == 1
        and int(counts["not_attempted_count"]) == 1
        and int(counts["failed_count"]) == 0
        and int(counts["partial_effect_count"]) == 0,
        "serial UNKNOWN count equation mismatch",
    )

    items = _by_sku(commit_result["items"])
    _check(
        set(items) == {"AISHA-B-60-Z", "AISHA-C-55-Z", "AISHA-D-50-Z"},
        "commit SKU set mismatch",
    )
    verified = items["AISHA-C-55-Z"]
    unknown = items["AISHA-D-50-Z"]
    stopped = items["AISHA-B-60-Z"]
    _check(
        verified["operation_result"] == "VERIFIED"
        and verified["listing_effect_state"] == "VERIFIED"
        and verified["action_confirm_clicked"] is True
        and bool(verified["action_clicked_at"])
        and bool(verified["readback_observed_at"]),
        "first executed item is not VERIFIED",
    )
    _check(
        unknown["operation_result"] == "NEEDS_RECONCILIATION"
        and unknown["listing_effect_state"] == "UNKNOWN"
        and unknown["action_confirm_clicked"] is True
        and bool(unknown["action_clicked_at"])
        and unknown["readback_observed_at"] is None,
        "second executed item is not UNKNOWN at the click boundary",
    )
    _check(
        stopped["operation_result"] == "NOT_ATTEMPTED"
        and stopped["listing_effect_state"] == "NOT_STARTED"
        and stopped["action_confirm_clicked"] is False
        and stopped["action_clicked_at"] is None
        and stopped["readback_observed_at"] is None,
        "remaining item was not stopped",
    )
    _check(
        _parse_time(str(verified["action_clicked_at"]))
        < _parse_time(str(unknown["action_clicked_at"])),
        "verified/UNKNOWN execution order is invalid",
    )

    _check(
        reconcile_request["execution_mode"] == "RECONCILE",
        "reconcile mode mismatch",
    )
    _check(len(reconcile_request["items"]) == 1, "reconcile is not single-item")
    _check(
        reconcile_request["items"][0]["internal_sku"] == "AISHA-D-50-Z",
        "reconcile targets the wrong SKU",
    )
    _check(
        reconcile_result["status"] == "VERIFIED"
        and reconcile_result["batch_status"] == "VERIFIED",
        "reconcile is not VERIFIED",
    )
    reconcile_item = reconcile_result["items"][0]
    _check(
        reconcile_item["operation_result"] == "VERIFIED"
        and reconcile_item["action_confirm_clicked"] is False
        and reconcile_item["action_clicked_at"] is None
        and bool(reconcile_item["readback_observed_at"]),
        "reconcile was not read-only VERIFIED",
    )

    expected = manifest["expected"]
    _check(
        expected["commit_batch_id"] == commit_request["batch_id"],
        "manifest commit batch mismatch",
    )
    _check(
        expected["commit_execution_attempt_id"]
        == commit_request["execution_attempt_id"],
        "manifest commit attempt mismatch",
    )
    _check(
        expected["reconcile_execution_attempt_id"]
        == reconcile_request["execution_attempt_id"],
        "manifest reconcile attempt mismatch",
    )
    _check(
        commit_request["mapping_source_version"]
        == "sha256:" + _sha256(common["mapping"]),
        "mapping binding mismatch",
    )

    db_attempts = {
        str(row["execution_attempt_id"]): row
        for row in database["execution_attempts"]
    }
    request_items = _by_sku(commit_request["items"])
    _check(
        db_attempts[
            str(request_items["AISHA-C-55-Z"]["item_execution_attempt_id"])
        ]["status"]
        == "VERIFIED",
        "database lost original VERIFIED attempt",
    )
    _check(
        db_attempts[
            str(request_items["AISHA-D-50-Z"]["item_execution_attempt_id"])
        ]["status"]
        == "UNKNOWN",
        "database lost original UNKNOWN attempt",
    )
    _check(
        db_attempts[
            str(request_items["AISHA-B-60-Z"]["item_execution_attempt_id"])
        ]["status"]
        == "NOT_ATTEMPTED",
        "database lost original NOT_ATTEMPTED attempt",
    )
    _check(
        db_attempts[
            str(reconcile_request["items"][0]["item_execution_attempt_id"])
        ]["status"]
        == "VERIFIED",
        "database lost reconcile attempt",
    )
    _check(
        database["batch"]["status"] == "PARTIAL"
        and int(database["batch"]["verified_count"]) == 2
        and int(database["batch"]["not_attempted_count"]) == 1
        and int(database["batch"]["unknown_count"]) == 0,
        "database final batch projection mismatch",
    )
    unknown_operation_id = str(
        request_items["AISHA-D-50-Z"]["operation_id"]
    )
    locks = {
        str(row["operation_id"]): row for row in database["write_locks"]
    }
    _check(
        locks[unknown_operation_id]["status"] == "RELEASED",
        "UNKNOWN write lock was not released by reconcile",
    )
    _check(
        int(database["open_review_count"]) == 0,
        "reconciled UNKNOWN still has an open review",
    )

    return {
        "bundle_id": bundle_dir.name,
        "status": "VERIFIED",
        "commit_batch_id": commit_request["batch_id"],
        "commit_execution_attempt_id": commit_request[
            "execution_attempt_id"
        ],
        "verified_internal_sku": "AISHA-C-55-Z",
        "unknown_internal_sku": "AISHA-D-50-Z",
        "not_attempted_internal_sku": "AISHA-B-60-Z",
        "reconcile_execution_attempt_id": reconcile_request[
            "execution_attempt_id"
        ],
        "sanitized_sha256": {
            path.name: _sha256(path)
            for path in bundle_dir.iterdir()
            if path.is_file() and path.name != "validation_report.json"
        },
        "checks": {
            "two_stage_contract_binding_valid": True,
            "verified_then_unknown_then_stopped_valid": True,
            "strict_execution_order_valid": True,
            "complete_result_skeleton_valid": True,
            "single_read_only_reconcile_valid": True,
            "database_original_attempts_preserved_valid": True,
            "write_lock_released_after_reconcile_valid": True,
            "receipt_and_ack_binding_valid": True,
            "redaction_valid": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    args = parser.parse_args()
    report = validate_serial_unknown_bundle(args.bundle)
    print(json.dumps({"ok": True, **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

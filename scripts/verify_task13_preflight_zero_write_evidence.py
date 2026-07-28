from __future__ import annotations

import argparse
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
    "PREFLIGHT-ZERO-WRITE-AISHA-A-E-20260727"
)


def _by_sku(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["internal_sku"]): row for row in rows}


def validate_preflight_zero_write_bundle(
    bundle_dir: Path,
) -> dict[str, Any]:
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

    stage = _validate_stage(bundle_dir, "commit")
    request = stage["request"]
    result = stage["result"]
    phase = stage["phase"]
    _validate_count_equation(result, stage="commit")

    _check(request["execution_mode"] == "COMMIT", "execution mode mismatch")
    _check(request["action_type"] == "set_online", "action type mismatch")
    _check(len(request["items"]) == 2, "request is not a two-item batch")
    _check(len(result["items"]) == 2, "result is not a two-item batch")
    _check(result["status"] == "FAILED", "result is not FAILED")
    _check(result["batch_status"] == "FAILED", "batch is not FAILED")
    _check(
        result["error_code"] == "LISTING_DATA_MISMATCH",
        "batch did not fail on LISTING_DATA_MISMATCH",
    )
    _check(
        result["business_operation_completed"] is False,
        "batch incorrectly reports a completed write",
    )
    _check(
        result["side_effect_state"] == "NOT_APPLIED",
        "batch side-effect state is not NOT_APPLIED",
    )
    _check(
        phase["detail_effect_state"] == "NOT_STARTED"
        and phase["listing_effect_state"] == "NOT_STARTED",
        "terminal phase reports a write side effect",
    )

    counts = result["counts"]
    _check(
        int(counts["batch_target_count"]) == 2
        and int(counts["attempted_count"]) == 0
        and int(counts["verified_count"]) == 0
        and int(counts["failed_count"]) == 1
        and int(counts["not_applied_count"]) == 1
        and int(counts["not_attempted_count"]) == 1
        and int(counts["unknown_count"]) == 0
        and int(counts["partial_effect_count"]) == 0,
        "preflight zero-write count equation mismatch",
    )

    request_items = _by_sku(request["items"])
    result_items = _by_sku(result["items"])
    expected_skus = {"AISHA-A-70-Z", "AISHA-E-45-Z"}
    _check(set(request_items) == expected_skus, "request SKU set mismatch")
    _check(set(result_items) == expected_skus, "result SKU set mismatch")
    for item in result_items.values():
        _check(item["detail_save_clicked"] is False, "detail save was clicked")
        _check(
            item["detail_save_clicked_at"] is None,
            "detail save timestamp exists",
        )
        _check(
            item["action_confirm_clicked"] is False,
            "listing confirmation was clicked",
        )
        _check(item["action_clicked_at"] is None, "action timestamp exists")

    normal = result_items["AISHA-A-70-Z"]
    _check(
        normal["operation_result"] == "NOT_ATTEMPTED"
        and normal["detail_effect_state"] == "NOT_STARTED"
        and normal["listing_effect_state"] == "NOT_STARTED",
        "normal item was not preserved as NOT_ATTEMPTED",
    )
    _check(
        normal["observed_price_before_action"] == "18.00"
        and int(normal["observed_inventory_before_action"]) == 28,
        "normal item preflight observation mismatch",
    )

    mismatch = result_items["AISHA-E-45-Z"]
    _check(
        mismatch["operation_result"] == "NOT_APPLIED"
        and mismatch["detail_effect_state"] == "NOT_APPLIED"
        and mismatch["listing_effect_state"] == "NOT_APPLIED",
        "mismatch item was not preserved as NOT_APPLIED",
    )
    _check(
        mismatch["error_code"] == "LISTING_DATA_MISMATCH",
        "mismatch item error code changed",
    )
    _check(
        mismatch["observed_price_before_action"] == "7.50"
        and int(mismatch["observed_inventory_before_action"]) == 2,
        "mismatch item preflight observation mismatch",
    )
    mismatch_request = request_items["AISHA-E-45-Z"]
    _check(
        mismatch_request["target_price"] == "7.00"
        and int(mismatch_request["target_inventory"]) == 1,
        "mismatch item contract changed",
    )

    expected = manifest["expected"]
    _check(expected["batch_id"] == request["batch_id"], "manifest batch mismatch")
    _check(
        expected["execution_attempt_id"] == request["execution_attempt_id"],
        "manifest attempt mismatch",
    )
    _check(
        expected["normal_internal_sku"] == "AISHA-A-70-Z"
        and expected["mismatch_internal_sku"] == "AISHA-E-45-Z",
        "manifest role binding mismatch",
    )
    _check(
        request["mapping_source_version"]
        == "sha256:" + _sha256(common["mapping"]),
        "mapping binding mismatch",
    )

    database_items = _by_sku(database["batch_items"])
    database_tasks = {
        str(row["task_id"]): row for row in database["tasks"]
    }
    database_operations = {
        str(row["operation_id"]): row for row in database["operations"]
    }
    database_attempts = {
        str(row["operation_id"]): row for row in database["execution_attempts"]
    }
    database_locks = {
        str(row["operation_id"]): row for row in database["write_locks"]
    }
    _check(set(database_items) == expected_skus, "database item set mismatch")
    _check(
        database["batch"]["status"] == "FAILED"
        and int(database["batch"]["batch_target_count"]) == 2
        and int(database["batch"]["failed_count"]) == 1
        and int(database["batch"]["not_attempted_count"]) == 1,
        "database batch ledger mismatch",
    )
    for sku, request_item in request_items.items():
        operation_id = str(request_item["operation_id"])
        db_item = database_items[sku]
        _check(
            db_item["detail_save_clicked_at"] is None
            and db_item["action_clicked_at"] is None,
            f"database item contains a write click: {sku}",
        )
        _check(
            database_locks[operation_id]["status"] == "RELEASED",
            f"write lock was not released: {sku}",
        )

    normal_request = request_items["AISHA-A-70-Z"]
    normal_operation_id = str(normal_request["operation_id"])
    _check(
        database_tasks[str(normal_request["source_task_id"])]["task_status"]
        == "pending",
        "normal task was not returned to pending",
    )
    _check(
        database_operations[normal_operation_id]["status"] == "PENDING"
        and database_operations[normal_operation_id]["operation_result"] == "",
        "normal operation was not preserved as pending",
    )
    _check(
        database_attempts[normal_operation_id]["status"] == "NOT_ATTEMPTED"
        and database_attempts[normal_operation_id]["side_effect_state"]
        == "NOT_STARTED",
        "normal attempt ledger mismatch",
    )

    mismatch_request = request_items["AISHA-E-45-Z"]
    mismatch_operation_id = str(mismatch_request["operation_id"])
    _check(
        database_tasks[str(mismatch_request["source_task_id"])]["task_status"]
        == "failed",
        "mismatch task is not failed",
    )
    _check(
        database_operations[mismatch_operation_id]["status"] == "FAILED"
        and database_operations[mismatch_operation_id]["operation_result"]
        == "NOT_APPLIED",
        "mismatch operation ledger mismatch",
    )
    _check(
        database_attempts[mismatch_operation_id]["status"] == "FAILED"
        and database_attempts[mismatch_operation_id]["side_effect_state"]
        == "NOT_APPLIED",
        "mismatch attempt ledger mismatch",
    )
    _check(
        int(database["open_review_count"]) == 0,
        "zero-write preflight failure created an open review",
    )

    return {
        "bundle_id": bundle_dir.name,
        "status": "VERIFIED",
        "batch_id": request["batch_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "normal_internal_sku": "AISHA-A-70-Z",
        "mismatch_internal_sku": "AISHA-E-45-Z",
        "batch_result": result["batch_status"],
        "write_click_count": 0,
        "sanitized_sha256": {
            path.name: _sha256(path)
            for path in bundle_dir.iterdir()
            if path.is_file() and path.name != "validation_report.json"
        },
        "checks": {
            "contract_binding_valid": True,
            "batch_preflight_all_or_nothing_valid": True,
            "normal_item_not_attempted_valid": True,
            "mismatch_item_not_applied_valid": True,
            "zero_detail_save_clicks_valid": True,
            "zero_listing_action_clicks_valid": True,
            "zero_final_confirmation_clicks_valid": True,
            "database_ledger_valid": True,
            "write_locks_released_valid": True,
            "no_open_review_valid": True,
            "receipt_and_ack_binding_valid": True,
            "redaction_valid": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    args = parser.parse_args()
    report = validate_preflight_zero_write_bundle(args.bundle)
    print(json.dumps({"ok": True, **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    _validate_stage,
)


DEFAULT_BUNDLE = Path(
    "docs/evidence/task13/"
    "ALREADY-APPLIED-AISHA-B-60-Z-20260727"
)


def validate_already_applied_bundle(
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
    _check(request["execution_mode"] == "COMMIT", "execution mode mismatch")
    _check(request["action_type"] == "set_offline", "action type mismatch")
    _check(result["status"] == "VERIFIED", "result is not VERIFIED")
    _check(
        result["batch_status"] == "VERIFIED",
        "batch result is not VERIFIED",
    )
    _check(
        result["business_operation_completed"] is False,
        "result incorrectly reports a completed write",
    )
    _check(
        result["side_effect_state"] == "NOT_STARTED",
        "batch side effect was started",
    )
    _check(len(request["items"]) == 1, "request is not single-item")
    _check(len(result["items"]) == 1, "result is not single-item")
    request_item = request["items"][0]
    result_item = result["items"][0]
    sku = str(request_item["internal_sku"])
    _check(
        result_item["operation_result"] == "ALREADY_APPLIED",
        "item is not ALREADY_APPLIED",
    )
    _check(
        result_item["detail_effect_state"] == "NOT_APPLIED"
        and result_item["listing_effect_state"] == "NOT_APPLIED",
        "item reports a write side effect",
    )
    _check(
        result_item["detail_save_clicked"] is False
        and result_item["detail_save_clicked_at"] is None,
        "detail save was clicked",
    )
    _check(
        result_item["action_confirm_clicked"] is False
        and result_item["action_clicked_at"] is None,
        "listing action or final confirmation was clicked",
    )
    _check(
        bool(result_item["readback_observed_at"]),
        "item has no readback timestamp",
    )
    counts = result["counts"]
    _check(
        int(counts["batch_target_count"]) == 1
        and int(counts["verified_count"]) == 1
        and int(counts["already_applied_count"]) == 1
        and int(counts["attempted_count"]) == 0
        and int(counts["verified_applied_count"]) == 0
        and int(counts["failed_count"]) == 0
        and int(counts["unknown_count"]) == 0
        and int(counts["partial_effect_count"]) == 0
        and int(counts["not_attempted_count"]) == 0,
        "ALREADY_APPLIED count equation mismatch",
    )

    expected = manifest["expected"]
    _check(expected["internal_sku"] == sku, "manifest SKU mismatch")
    _check(expected["batch_id"] == request["batch_id"], "manifest batch mismatch")
    _check(
        expected["operation_id"] == request_item["operation_id"],
        "manifest operation mismatch",
    )
    _check(
        expected["execution_attempt_id"] == request["execution_attempt_id"],
        "manifest attempt mismatch",
    )
    _check(database["internal_sku"] == sku, "database SKU mismatch")
    _check(
        database["task"]["task_status"] == "success",
        "database task is not successful",
    )
    _check(
        database["batch"]["status"] == "VERIFIED"
        and int(database["batch"]["batch_target_count"]) == 1
        and int(database["batch"]["verified_count"]) == 1,
        "database batch is not VERIFIED",
    )
    _check(
        database["item"]["operation_result"] == "VERIFIED"
        and database["item"]["detail_effect_state"] == "NOT_APPLIED"
        and database["item"]["listing_effect_state"] == "NOT_APPLIED"
        and database["item"]["detail_save_clicked_at"] is None
        and database["item"]["action_clicked_at"] is None,
        "database item does not preserve the zero-write result",
    )
    _check(
        database["operation"]["status"] == "VERIFIED"
        and database["operation"]["operation_result"] == "VERIFIED",
        "database operation is not VERIFIED",
    )
    _check(
        database["execution_attempt"]["execution_mode"] == "COMMIT"
        and database["execution_attempt"]["status"] == "VERIFIED"
        and database["execution_attempt"]["side_effect_state"] == "NOT_APPLIED",
        "database attempt does not preserve NOT_APPLIED",
    )
    raw_output = json.loads(
        database["execution_attempt"]["raw_output_json"]
    )
    _check(
        raw_output["operation_result"] == "ALREADY_APPLIED",
        "database raw attempt lost ALREADY_APPLIED",
    )
    _check(
        database["write_lock"]["status"] == "RELEASED",
        "database write lock is not released",
    )
    _check(
        int(database["open_review_count"]) == 0,
        "ALREADY_APPLIED created an open review",
    )
    _check(
        request["mapping_source_version"]
        == "sha256:" + _sha256(common["mapping"]),
        "mapping binding mismatch",
    )

    return {
        "bundle_id": bundle_dir.name,
        "internal_sku": sku,
        "status": "VERIFIED",
        "operation_result": "ALREADY_APPLIED",
        "batch_id": request["batch_id"],
        "operation_id": request_item["operation_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "item_execution_attempt_id": request_item[
            "item_execution_attempt_id"
        ],
        "sanitized_sha256": {
            path.name: _sha256(path)
            for path in bundle_dir.iterdir()
            if path.is_file() and path.name != "validation_report.json"
        },
        "checks": {
            "contract_binding_valid": True,
            "already_applied_valid": True,
            "zero_detail_save_clicks_valid": True,
            "zero_listing_action_clicks_valid": True,
            "zero_final_confirmation_clicks_valid": True,
            "database_ledger_valid": True,
            "write_lock_released_valid": True,
            "no_open_review_valid": True,
            "receipt_and_ack_binding_valid": True,
            "redaction_valid": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    args = parser.parse_args()
    report = validate_already_applied_bundle(args.bundle)
    print(json.dumps({"ok": True, **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

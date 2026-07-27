from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.shadowbot_listing_action_contract import (  # noqa: E402
    validate_listing_action_phase,
    validate_listing_action_request,
    validate_listing_action_result,
)


DEFAULT_BUNDLE = Path(
    "docs/evidence/task13/"
    "UNKNOWN-RECONCILE-AISHA-B-60-Z-20260727"
)
WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
WINDOWS_PATH_ANYWHERE = re.compile(r"[A-Za-z]:[\\/]")
STAGES = ("commit", "reconcile")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _assert_sanitized(value: Any, *, key: str = "") -> None:
    if isinstance(value, dict):
        for name, item in value.items():
            _assert_sanitized(item, key=name)
        return
    if isinstance(value, list):
        for item in value:
            _assert_sanitized(item, key=key)
        return
    if not isinstance(value, str):
        return
    _check(
        not WINDOWS_PATH.match(value) and not value.startswith("\\\\"),
        f"unsanitized path remains in {key}",
    )
    if key in {"worker_id", "device_id", "robot_id", "computer_name"}:
        _check(value == "<REDACTED_DEVICE>", f"{key} was not redacted")


def _stage_files(bundle_dir: Path, stage: str) -> dict[str, Path]:
    return {
        "request": bundle_dir / f"{stage}.request.sanitized.json",
        "result": bundle_dir / f"{stage}.result.sanitized.json",
        "phase": bundle_dir / f"{stage}.phase.sanitized.json",
        "receipt": bundle_dir / f"{stage}.receipt.sanitized.json",
        "ack": bundle_dir / f"{stage}.ack.sanitized.json",
        "report": bundle_dir / f"{stage}.report.sanitized.md",
    }


def _validate_stage(
    bundle_dir: Path,
    stage: str,
) -> dict[str, Any]:
    files = _stage_files(bundle_dir, stage)
    for path in files.values():
        _check(path.is_file(), f"missing evidence file: {path}")
    request = _read_json(files["request"])
    result = _read_json(files["result"])
    phase = _read_json(files["phase"])
    receipt = _read_json(files["receipt"])
    ack = _read_json(files["ack"])
    report = files["report"].read_text(encoding="utf-8")
    for document in (request, result, phase, receipt, ack):
        _assert_sanitized(document)
    _check(
        not WINDOWS_PATH_ANYWHERE.search(report),
        f"{stage} report contains an unsanitized path",
    )

    request_sha = "sha256:" + _sha256(files["request"])
    validate_listing_action_request(request, check_expiry=False)
    validate_listing_action_phase(
        request,
        phase,
        request_file_sha256=request_sha,
    )
    validate_listing_action_result(
        result,
        request=request,
        request_file_sha256=request_sha,
    )
    _check(
        phase["phase"] == "RESULT_WRITTEN",
        f"{stage} terminal phase is not RESULT_WRITTEN",
    )
    _check(
        receipt["result_sha256"] == _sha256(files["result"]),
        f"{stage} receipt result hash mismatch",
    )
    _check(receipt["ack_state"] == "WRITTEN", f"{stage} ACK is not written")
    _check(
        ack["result_file_sha256"] == _sha256(files["result"]),
        f"{stage} ACK result hash mismatch",
    )
    for document, label in ((receipt, "receipt"), (ack, "ack")):
        for field in ("batch_id", "execution_attempt_id", "result_id"):
            _check(
                str(document.get(field) or "")
                == str(result.get(field) or ""),
                f"{stage} {label} {field} mismatch",
            )
    for identifier in (
        request["batch_id"],
        request["execution_attempt_id"],
    ):
        _check(identifier in report, f"{stage} report omits {identifier}")
    return {
        "request": request,
        "result": result,
        "phase": phase,
        "files": files,
    }


def _validate_count_equation(
    result: dict[str, Any],
    *,
    stage: str,
) -> None:
    counts = result["counts"]
    classified = sum(
        int(counts[name])
        for name in (
            "verified_count",
            "failed_count",
            "unknown_count",
            "partial_effect_count",
            "not_attempted_count",
        )
    )
    _check(
        classified == int(counts["batch_target_count"]),
        f"{stage} batch count equation failed",
    )
    _check(
        int(counts["batch_target_count"]) == len(result["items"]),
        f"{stage} result item count differs from batch target count",
    )


def validate_unknown_reconcile_bundle(
    bundle_dir: Path,
    *,
    expected_final_operation_result: str = "VERIFIED",
) -> dict[str, Any]:
    _check(
        expected_final_operation_result in {"VERIFIED", "NOT_APPLIED"},
        "unsupported final reconcile outcome",
    )
    expected_terminal_status = (
        "VERIFIED"
        if expected_final_operation_result == "VERIFIED"
        else "FAILED"
    )
    expected_task_status = (
        "success"
        if expected_final_operation_result == "VERIFIED"
        else "failed"
    )
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

    stages = {
        stage: _validate_stage(bundle_dir, stage)
        for stage in STAGES
    }
    commit = stages["commit"]
    reconcile = stages["reconcile"]
    commit_request = commit["request"]
    commit_result = commit["result"]
    reconcile_request = reconcile["request"]
    reconcile_result = reconcile["result"]

    _check(commit_request["execution_mode"] == "COMMIT", "commit mode mismatch")
    _check(commit_result["status"] == "UNKNOWN", "commit is not UNKNOWN")
    _check(
        commit_result["side_effect_state"] == "UNKNOWN",
        "commit side effect is not UNKNOWN",
    )
    _check(
        commit_result["error_code"]
        == "CONTROLLED_AFTER_ACTION_CLICK_UNKNOWN",
        "commit does not contain the controlled UNKNOWN boundary",
    )
    _check(
        reconcile_request["execution_mode"] == "RECONCILE",
        "reconcile mode mismatch",
    )
    _check(
        reconcile_result["status"] == expected_terminal_status,
        "reconcile terminal status mismatch",
    )
    _check(
        reconcile_result["side_effect_state"]
        == expected_final_operation_result,
        "reconcile side effect mismatch",
    )
    _check(
        commit_request["batch_id"] == reconcile_request["batch_id"],
        "commit/reconcile batch differs",
    )
    _check(
        commit_request["action_type"] == reconcile_request["action_type"],
        "commit/reconcile action differs",
    )
    _check(
        len(commit_request["items"]) == 1
        and len(reconcile_request["items"]) == 1,
        "evidence is not a single-item controlled UNKNOWN sample",
    )
    commit_request_item = commit_request["items"][0]
    reconcile_request_item = reconcile_request["items"][0]
    commit_result_item = commit_result["items"][0]
    reconcile_result_item = reconcile_result["items"][0]
    sku = str(commit_request_item["internal_sku"])
    for field in (
        "internal_sku",
        "operation_id",
        "source_task_id",
        "item_payload_sha256",
        "write_identity_key",
    ):
        _check(
            commit_request_item[field] == reconcile_request_item[field],
            f"commit/reconcile item {field} differs",
        )
    _check(
        reconcile_request["operation_id"] == commit_request_item["operation_id"],
        "reconcile operation binding mismatch",
    )
    _check(
        reconcile_request["source_execution_attempt_id"]
        == commit_request_item["item_execution_attempt_id"],
        "reconcile source attempt is not the original item attempt",
    )
    _check(
        reconcile_request["source_result_id"] == commit_result["result_id"],
        "reconcile source result binding mismatch",
    )
    _check(
        commit_result_item["operation_result"] == "NEEDS_RECONCILIATION",
        "commit item does not require reconciliation",
    )
    _check(
        commit_result_item["listing_effect_state"] == "UNKNOWN",
        "commit listing effect is not UNKNOWN",
    )
    _check(
        commit_result_item["action_confirm_clicked"] is True
        and bool(commit_result_item["action_clicked_at"]),
        "commit final confirmation boundary is missing",
    )
    _check(
        commit_result_item["detail_save_clicked"] is False,
        "controlled set_offline sample unexpectedly saved details",
    )
    _check(
        reconcile_result_item["operation_result"]
        == expected_final_operation_result,
        "reconcile item operation result mismatch",
    )
    _check(
        reconcile_result_item["listing_effect_state"]
        == expected_final_operation_result,
        "reconcile listing effect mismatch",
    )
    _check(
        reconcile_result_item["action_confirm_clicked"] is False
        and reconcile_result_item["action_clicked_at"] is None
        and reconcile_result_item["detail_save_clicked"] is False,
        "RECONCILE performed a write click",
    )
    _check(
        bool(reconcile_result_item["readback_observed_at"]),
        "RECONCILE has no independent readback timestamp",
    )
    _validate_count_equation(commit_result, stage="commit")
    _validate_count_equation(reconcile_result, stage="reconcile")

    expected = manifest["expected"]
    _check(expected["internal_sku"] == sku, "manifest SKU mismatch")
    _check(
        expected["batch_id"] == commit_request["batch_id"],
        "manifest batch mismatch",
    )
    _check(
        expected["operation_id"] == commit_request_item["operation_id"],
        "manifest operation mismatch",
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
        expected["final_operation_result"]
        == expected_final_operation_result,
        "manifest final operation result mismatch",
    )
    _check(
        expected["final_task_status"] == expected_task_status,
        "manifest final task status mismatch",
    )
    _check(
        expected["final_write_lock_status"] == "RELEASED",
        "manifest final write-lock status mismatch",
    )

    _check(database["internal_sku"] == sku, "database SKU mismatch")
    _check(
        database["batch"]["status"] == expected_terminal_status,
        "database batch terminal status mismatch",
    )
    expected_verified_count = (
        1 if expected_final_operation_result == "VERIFIED" else 0
    )
    expected_failed_count = 1 - expected_verified_count
    _check(
        int(database["batch"]["batch_target_count"]) == 1
        and int(database["batch"]["verified_count"])
        == expected_verified_count
        and int(database["batch"]["failed_count"])
        == expected_failed_count
        and int(database["batch"]["unknown_count"]) == 0
        and int(database["batch"]["partial_effect_count"]) == 0
        and int(database["batch"]["not_attempted_count"]) == 0,
        "database batch final count equation mismatch",
    )
    _check(
        database["item"]["operation_result"]
        == expected_final_operation_result
        and database["item"]["listing_effect_state"]
        == expected_final_operation_result,
        "database item final result mismatch",
    )
    _check(
        database["operation"]["status"] == expected_terminal_status
        and database["operation"]["operation_result"]
        == expected_final_operation_result,
        "database operation final result mismatch",
    )
    _check(
        database["task"]["task_status"] == expected_task_status,
        "database task final status mismatch",
    )
    _check(
        database["write_lock"]["status"] == "RELEASED",
        "database write lock is not released",
    )
    attempts = database["execution_attempts"]
    _check(len(attempts) == 2, "database does not contain exactly two attempts")
    _check(
        [attempt["execution_mode"] for attempt in attempts]
        == ["COMMIT", "RECONCILE"],
        "database attempt order/modes differ",
    )
    _check(
        [attempt["status"] for attempt in attempts]
        == ["UNKNOWN", expected_terminal_status],
        "database attempt statuses differ",
    )
    reviews = database["reviews"]
    _check(len(reviews) == 1, "database review count is not one")
    _check(
        reviews[0]["review_status"] == "cancelled"
        and reviews[0]["resolved_by"] == "system:listing_reconcile",
        "database review was not closed by listing reconcile",
    )
    _check(
        database["attempt_mode_counts"] == {"COMMIT": 1, "RECONCILE": 1},
        "database attempt mode count mismatch",
    )

    mapping_sha = "sha256:" + _sha256(common["mapping"])
    for stage in stages.values():
        _check(
            stage["request"]["mapping_source_version"] == mapping_sha,
            "mapping binding mismatch",
        )

    return {
        "bundle_id": bundle_dir.name,
        "internal_sku": sku,
        "status": expected_final_operation_result,
        "batch_id": commit_request["batch_id"],
        "operation_id": commit_request_item["operation_id"],
        "commit_execution_attempt_id": commit_request["execution_attempt_id"],
        "commit_item_execution_attempt_id": commit_request_item[
            "item_execution_attempt_id"
        ],
        "reconcile_execution_attempt_id": reconcile_request[
            "execution_attempt_id"
        ],
        "reconcile_item_execution_attempt_id": reconcile_request_item[
            "item_execution_attempt_id"
        ],
        "sanitized_sha256": {
            path.name: _sha256(path)
            for path in bundle_dir.iterdir()
            if path.is_file() and path.name != "validation_report.json"
        },
        "checks": {
            "two_stage_contract_binding_valid": True,
            "controlled_unknown_click_boundary_valid": True,
            "single_read_only_reconcile_valid": True,
            "final_database_ledger_valid": True,
            "write_lock_released_valid": True,
            "review_closed_by_reconcile_valid": True,
            "count_equations_valid": True,
            "receipt_and_ack_binding_valid": True,
            "redaction_valid": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    args = parser.parse_args()
    report = validate_unknown_reconcile_bundle(args.bundle)
    print(json.dumps({"ok": True, **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

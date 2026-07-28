from __future__ import annotations

import argparse
from datetime import datetime
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
    "ROUND-TRIP-AISHA-A-70-Z-20260726"
)
WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
WINDOWS_PATH_ANYWHERE = re.compile(r"[A-Za-z]:[\\/]")
STAGES = ("set_online", "set_offline", "post_sync")


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
    validate_listing_action_request(
        request,
        check_expiry=False,
        allow_legacy_operation_id=True,
    )
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
    _check(result["status"] == "VERIFIED", f"{stage} is not VERIFIED")
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


def validate_round_trip_bundle(bundle_dir: Path) -> dict[str, Any]:
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
    online = stages["set_online"]
    offline = stages["set_offline"]
    sync = stages["post_sync"]

    _check(
        online["request"]["action_type"] == "set_online",
        "online stage action type mismatch",
    )
    _check(
        offline["request"]["action_type"] == "set_offline",
        "offline stage action type mismatch",
    )
    _check(
        sync["request"]["action_type"] == "sync_status",
        "post-sync stage action type mismatch",
    )
    _check(sync["request"]["execution_mode"] == "READ_ONLY", "post-sync is not read-only")
    _check(sync["request"]["items"] == [], "post-sync contains write items")
    _check(sync["result"]["side_effect_state"] == "NOT_STARTED", "post-sync reports a write")

    online_request_item = online["request"]["items"][0]
    offline_request_item = offline["request"]["items"][0]
    online_result_item = online["result"]["items"][0]
    offline_result_item = offline["result"]["items"][0]
    sku = str(online_request_item["internal_sku"])
    _check(len(online["request"]["items"]) == 1, "online evidence is not single-item")
    _check(len(offline["request"]["items"]) == 1, "offline evidence is not single-item")
    _check(
        sku == str(offline_request_item["internal_sku"]),
        "online/offline SKU differs",
    )
    _check(online_request_item["target_status"] == "online", "online target mismatch")
    _check(offline_request_item["target_status"] == "offline", "offline target mismatch")
    for item, label in (
        (online_result_item, "online"),
        (offline_result_item, "offline"),
    ):
        _check(item["operation_result"] == "VERIFIED", f"{label} item is not VERIFIED")
        _check(item["listing_effect_state"] == "VERIFIED", f"{label} listing effect is not VERIFIED")
        _check(item["action_confirm_clicked"] is True, f"{label} final confirmation was not clicked")
    _check(
        str(online_result_item["actual_price"])
        == str(online_request_item["target_price"]),
        "online price backread mismatch",
    )
    _check(
        int(online_result_item["actual_inventory"])
        == int(online_request_item["target_inventory"]),
        "online inventory backread mismatch",
    )
    _check(
        str(offline_result_item["observed_price_before_action"])
        == str(online_result_item["actual_price"]),
        "offline pre-action price differs from online backread",
    )
    _check(
        int(offline_result_item["observed_inventory_before_action"])
        == int(online_result_item["actual_inventory"]),
        "offline pre-action inventory differs from online backread",
    )

    snapshot = sync["result"]["snapshot"]
    _check(snapshot["snapshot_complete"] is True, "post-sync snapshot is incomplete")
    target_items = [
        item
        for item in snapshot["items"]
        if str(item.get("internal_sku") or "") == sku
    ]
    _check(len(target_items) == 1, "post-sync target item is not unique")
    target = target_items[0]
    _check(target["listing_location"] == "waiting_only", "post-sync target is not waiting_only")
    _check(int(target["online_occurrences"]) == 0, "post-sync target remains online")
    _check(int(target["waiting_occurrences"]) == 1, "post-sync target is not unique in waiting")
    _check(
        str(target["waiting_observed_price"])
        == str(offline_result_item["observed_price_before_action"]),
        "post-sync waiting price mismatch",
    )
    _check(
        int(target["waiting_observed_inventory"])
        == int(offline_result_item["observed_inventory_before_action"]),
        "post-sync waiting inventory mismatch",
    )

    timestamps = [
        datetime.fromisoformat(
            str(online_result_item["action_clicked_at"]).replace("Z", "+00:00")
        ),
        datetime.fromisoformat(
            str(offline_result_item["action_clicked_at"]).replace("Z", "+00:00")
        ),
        datetime.fromisoformat(
            str(snapshot["scan_completed_at"]).replace("Z", "+00:00")
        ),
    ]
    _check(timestamps == sorted(timestamps), "round-trip timestamps are out of order")

    expected = manifest["expected"]
    _check(expected["internal_sku"] == sku, "manifest SKU mismatch")
    _check(
        expected["online_operation_id"]
        == online_result_item["operation_id"],
        "manifest online operation mismatch",
    )
    _check(
        expected["offline_operation_id"]
        == offline_result_item["operation_id"],
        "manifest offline operation mismatch",
    )
    _check(
        expected["post_sync_snapshot_id"] == snapshot["snapshot_id"],
        "manifest snapshot mismatch",
    )
    _check(database["internal_sku"] == sku, "database backread SKU mismatch")
    _check(database["listing_status"]["online_status"] == "offline", "database status is not offline")
    _check(
        database["listing_status"]["online_status_source_id"]
        == snapshot["snapshot_id"],
        "database status is not sourced from post-sync",
    )
    _check(
        database["post_sync_item"]["listing_location"] == "waiting_only",
        "database snapshot item is not waiting_only",
    )
    for action in ("set_online", "set_offline"):
        ledger = database[action]
        _check(ledger["batch"]["status"] == "VERIFIED", f"{action} batch is not VERIFIED")
        _check(ledger["item"]["operation_result"] == "VERIFIED", f"{action} ledger item is not VERIFIED")
        _check(ledger["task"]["task_status"] == "success", f"{action} task is not successful")
    _check(
        database["current_write_lock"]["status"] == "RELEASED",
        "current SKU write lock is not released",
    )
    _check(
        database["current_write_lock"]["operation_id"]
        == offline_result_item["operation_id"],
        "current SKU write lock does not belong to the final write",
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
        "status": "VERIFIED",
        "online_batch_id": online["request"]["batch_id"],
        "offline_batch_id": offline["request"]["batch_id"],
        "post_sync_batch_id": sync["request"]["batch_id"],
        "post_sync_snapshot_id": snapshot["snapshot_id"],
        "sanitized_sha256": {
            path.name: _sha256(path)
            for path in bundle_dir.iterdir()
            if path.is_file() and path.name != "validation_report.json"
        },
        "checks": {
            "three_stage_contract_binding_valid": True,
            "single_sku_round_trip_valid": True,
            "final_confirmation_and_readback_valid": True,
            "post_sync_waiting_only_valid": True,
            "database_ledger_backread_valid": True,
            "receipt_and_ack_binding_valid": True,
            "redaction_valid": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    args = parser.parse_args()
    report = validate_round_trip_bundle(args.bundle)
    print(json.dumps({"ok": True, **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

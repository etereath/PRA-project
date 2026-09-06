from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
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


DEFAULT_ROOT = Path("docs/evidence/task13")
WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
WINDOWS_PATH_ANYWHERE = re.compile(r"[A-Za-z]:[\\/]")


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


def validate_bundle(bundle_dir: Path) -> dict[str, Any]:
    required = {
        "request": bundle_dir / "request.sanitized.json",
        "result": bundle_dir / "result.sanitized.json",
        "phase": bundle_dir / "phase.sanitized.json",
        "receipt": bundle_dir / "receipt.sanitized.json",
        "ack": bundle_dir / "ack.sanitized.json",
        "database_projection": bundle_dir / "database_projection.sanitized.json",
        "mapping": bundle_dir / "product_identity_mapping.json",
        "manifest": bundle_dir / "evidence_manifest.json",
        "report": bundle_dir / "sync-report.sanitized.md",
    }
    for path in required.values():
        _check(path.is_file(), f"missing evidence file: {path}")

    request = _read_json(required["request"])
    result = _read_json(required["result"])
    phase = _read_json(required["phase"])
    receipt = _read_json(required["receipt"])
    ack = _read_json(required["ack"])
    database_projection = _read_json(required["database_projection"])
    mapping = _read_json(required["mapping"])
    manifest = _read_json(required["manifest"])
    report_text = required["report"].read_text(encoding="utf-8")

    for document in (
        request,
        result,
        phase,
        receipt,
        ack,
        database_projection,
        mapping,
        manifest,
    ):
        _assert_sanitized(document)
    _check(
        not WINDOWS_PATH_ANYWHERE.search(report_text),
        "report contains an unsanitized path",
    )

    validate_listing_action_request(request, check_expiry=False)
    request_sha = "sha256:" + _sha256(required["request"])
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

    _check(request["action_type"] == "sync_status", "request is not SYNC_STATUS")
    _check(request["execution_mode"] == "READ_ONLY", "request is not READ_ONLY")
    _check(request["items"] == [], "SYNC_STATUS request contains target items")
    _check(result["status"] == "VERIFIED", "result is not VERIFIED")
    _check(result["side_effect_state"] == "NOT_STARTED", "result reports a side effect")
    _check(
        result["business_operation_completed"] is False,
        "SYNC_STATUS reports a completed business write",
    )
    _check(phase["phase"] == "RESULT_WRITTEN", "terminal phase is not RESULT_WRITTEN")
    _check(
        phase["detail_effect_state"] == "NOT_STARTED"
        and phase["listing_effect_state"] == "NOT_STARTED",
        "phase reports a write side effect",
    )

    mapping_sha = "sha256:" + _sha256(required["mapping"])
    _check(
        request["mapping_source_version"] == mapping_sha,
        "request does not bind the checked-in mapping file",
    )
    _check(
        mapping.get("platform_name") == request["platform_name"],
        "mapping platform differs from request",
    )

    snapshot = result["snapshot"]
    for field in (
        "online_scan_complete",
        "waiting_scan_complete",
        "snapshot_complete",
        "online_end_marker_verified",
        "waiting_end_marker_verified",
    ):
        _check(snapshot.get(field) is True, f"snapshot completeness field is false: {field}")
    items = snapshot["items"]
    item_count = len(items)
    location_counts = Counter(str(item["listing_location"]) for item in items)
    online_observations = sum(int(item["online_occurrences"]) for item in items)
    waiting_observations = sum(int(item["waiting_occurrences"]) for item in items)
    _check(
        len({str(item["snapshot_item_id"]) for item in items}) == item_count,
        "snapshot item IDs are not unique",
    )
    _check(
        sum(location_counts.values()) == item_count,
        "listing-location count identity is invalid",
    )

    expected = manifest["expected"]
    _check(result["status"] == expected["status"], "result status changed")
    _check(request["execution_attempt_id"] == manifest["execution_attempt_id"], "attempt mismatch")
    _check(request["batch_id"] == manifest["batch_id"], "batch mismatch")
    _check(snapshot["snapshot_id"] == manifest["snapshot_id"], "snapshot mismatch")
    _check(result["result_id"] == manifest["result_id"], "result mismatch")
    _check(item_count == expected["snapshot_item_count"], "snapshot item count changed")
    _check(
        online_observations == expected["online_observations"],
        "online observation count changed",
    )
    _check(
        waiting_observations == expected["waiting_observations"],
        "waiting observation count changed",
    )
    _check(
        dict(sorted(location_counts.items())) == expected["location_counts"],
        "listing-location distribution changed",
    )

    sanitized_result_sha = _sha256(required["result"])
    for document, label in ((receipt, "receipt"), (ack, "ack")):
        for field in (
            "result_id",
            "batch_id",
            "execution_attempt_id",
        ):
            _check(
                str(document.get(field) or "") == str(result.get(field) or ""),
                f"{label} {field} does not bind the result",
            )
    _check(receipt["result_sha256"] == sanitized_result_sha, "receipt result hash mismatch")
    _check(
        receipt["instruction_hash"] == result["instruction_hash"],
        "receipt instruction hash mismatch",
    )
    _check(
        receipt["manifest_sha256"] == result["manifest_sha256"],
        "receipt manifest hash mismatch",
    )
    _check(receipt["ack_state"] == "WRITTEN", "receipt ACK state is not WRITTEN")
    _check(receipt["last_projection_error"] == "", "receipt contains a projection error")
    _check(ack["result_file_sha256"] == sanitized_result_sha, "ACK result hash mismatch")
    _check(ack["status"] == "VERIFIED", "ACK status is not VERIFIED")
    _check(ack["snapshot_id"] == snapshot["snapshot_id"], "ACK snapshot mismatch")

    projection = database_projection
    _check(projection["snapshot_id"] == snapshot["snapshot_id"], "projection snapshot mismatch")
    _check(projection["snapshot_item_count"] == item_count, "projection item count mismatch")
    _check(
        projection["location_counts"] == dict(sorted(location_counts.items())),
        "projection location counts mismatch",
    )
    for field in (
        "projected_listing_status_count",
        "open_anomaly_count",
        "related_review_count",
        "related_notification_count",
    ):
        _check(projection[field] == expected[field], f"projection count changed: {field}")
    projected_skus = projection["projected_internal_skus"]
    mapped_skus = {
        str(item["internal_sku"])
        for item in items
        if item.get("internal_sku") not in (None, "")
    }
    _check(
        len(projected_skus) == len(set(projected_skus)),
        "projected SKU list contains duplicates",
    )
    _check(
        len(projected_skus) == projection["projected_listing_status_count"],
        "projected SKU count identity is invalid",
    )
    _check(
        set(projected_skus).issubset(mapped_skus),
        "database projection contains a SKU absent from the snapshot",
    )
    _check(
        sum(int(value) for value in projection["anomaly_reason_counts"].values())
        == projection["open_anomaly_count"],
        "anomaly reason count identity is invalid",
    )
    _check(
        projection["open_anomaly_count"]
        == projection["related_review_count"]
        == projection["related_notification_count"],
        "anomaly/review/notification count identity is invalid",
    )
    for identifier in (
        manifest["execution_attempt_id"],
        manifest["batch_id"],
        manifest["snapshot_id"],
    ):
        _check(identifier in report_text, f"report does not contain {identifier}")

    return {
        "bundle_id": bundle_dir.name,
        "batch_id": request["batch_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "result_id": result["result_id"],
        "status": result["status"],
        "snapshot_item_count": item_count,
        "online_observations": online_observations,
        "waiting_observations": waiting_observations,
        "location_counts": dict(sorted(location_counts.items())),
        "database_counts": {
            name: projection[name]
            for name in (
                "projected_listing_status_count",
                "open_anomaly_count",
                "related_review_count",
                "related_notification_count",
            )
        },
        "sanitized_sha256": {
            name: _sha256(path)
            for name, path in required.items()
        },
        "checks": {
            "request_contract_valid": True,
            "request_result_phase_binding_valid": True,
            "mapping_binding_valid": True,
            "two_page_completeness_valid": True,
            "read_only_side_effect_boundary_valid": True,
            "snapshot_count_identity_valid": True,
            "receipt_and_ack_binding_valid": True,
            "database_backread_count_identity_valid": True,
            "redaction_valid": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    bundle_dirs = sorted(
        path
        for path in args.root.iterdir()
        if path.is_dir() and path.name.startswith("ATTEMPT-")
    )
    _check(bundle_dirs, "Task 13 SYNC_STATUS evidence bundle is missing")
    reports = [validate_bundle(path) for path in bundle_dirs]
    print(
        json.dumps(
            {
                "ok": True,
                "bundle_count": len(reports),
                "bundles": [report["bundle_id"] for report in reports],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

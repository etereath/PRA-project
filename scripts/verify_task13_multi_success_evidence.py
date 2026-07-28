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
    "docs/evidence/task13/MULTI-SUCCESS-AISHA-E-CAPPUCCINO-E-20260726"
)
STAGES = ("set_online", "set_offline")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_multi_success_bundle(bundle_dir: Path) -> dict[str, Any]:
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

    stages = {name: _validate_stage(bundle_dir, name) for name in STAGES}
    expected_skus = {"AISHA-E-45-Z", "CAPPUCCINO-E-45-Z"}
    for stage_name, action_type in (
        ("set_online", "set_online"),
        ("set_offline", "set_offline"),
    ):
        stage = stages[stage_name]
        request = stage["request"]
        result = stage["result"]
        _validate_count_equation(result, stage=stage_name)
        _check(request["execution_mode"] == "COMMIT", "execution mode mismatch")
        _check(request["action_type"] == action_type, "action type mismatch")
        _check(result["status"] == "VERIFIED", "result is not VERIFIED")
        _check(result["batch_status"] == "VERIFIED", "batch is not VERIFIED")
        _check(
            result["business_operation_completed"] is True,
            "successful batch does not report a completed write",
        )
        _check(
            result["side_effect_state"] == "VERIFIED",
            "successful batch side-effect state is not VERIFIED",
        )
        _check(len(request["items"]) == 2, "request is not two-item")
        _check(len(result["items"]) == 2, "result is not two-item")
        _check(
            {str(item["internal_sku"]) for item in request["items"]}
            == expected_skus,
            "request SKU set mismatch",
        )
        _check(
            {str(item["internal_sku"]) for item in result["items"]}
            == expected_skus,
            "result SKU set mismatch",
        )
        counts = result["counts"]
        _check(
            int(counts["batch_target_count"]) == 2
            and int(counts["attempted_count"]) == 2
            and int(counts["verified_count"]) == 2
            and int(counts["verified_applied_count"]) == 2
            and int(counts["failed_count"]) == 0
            and int(counts["unknown_count"]) == 0
            and int(counts["partial_effect_count"]) == 0
            and int(counts["not_attempted_count"]) == 0,
            f"{stage_name} count equation mismatch",
        )

        action_times: list[datetime] = []
        save_action_pairs: list[tuple[datetime, datetime]] = []
        for item in result["items"]:
            _check(
                item["operation_result"] == "VERIFIED"
                and item["listing_effect_state"] == "VERIFIED",
                f"{stage_name} item is not VERIFIED",
            )
            _check(
                item["action_confirm_clicked"] is True
                and bool(item["action_clicked_at"])
                and bool(item["readback_observed_at"]),
                f"{stage_name} item lacks confirmation/readback",
            )
            action_time = _parse_time(str(item["action_clicked_at"]))
            action_times.append(action_time)
            if stage_name == "set_online":
                _check(
                    item["detail_save_clicked"] is True
                    and bool(item["detail_save_clicked_at"])
                    and item["detail_effect_state"] == "VERIFIED",
                    "SET_ONLINE item lacks verified detail save",
                )
                save_action_pairs.append(
                    (
                        _parse_time(str(item["detail_save_clicked_at"])),
                        action_time,
                    )
                )
            else:
                _check(
                    item["detail_save_clicked"] is False
                    and item["detail_save_clicked_at"] is None
                    and item["detail_effect_state"] == "NOT_APPLIED",
                    "SET_OFFLINE unexpectedly changed detail data",
                )
        _check(
            len(set(action_times)) == 2,
            f"{stage_name} action timestamps are not distinct",
        )
        if save_action_pairs:
            save_action_pairs.sort()
            _check(
                all(save < action for save, action in save_action_pairs),
                "SET_ONLINE detail save did not precede action",
            )
            _check(
                save_action_pairs[0][1] < save_action_pairs[1][0],
                "SET_ONLINE item trajectories overlapped",
            )

    expected = manifest["expected"]
    _check(
        set(expected["internal_skus"]) == expected_skus,
        "manifest SKU set mismatch",
    )
    for stage_name in STAGES:
        request = stages[stage_name]["request"]
        _check(
            expected[f"{stage_name}_batch_id"] == request["batch_id"],
            f"manifest batch mismatch: {stage_name}",
        )
        _check(
            expected[f"{stage_name}_execution_attempt_id"]
            == request["execution_attempt_id"],
            f"manifest attempt mismatch: {stage_name}",
        )

    _check(
        stages["set_online"]["request"]["mapping_source_version"]
        == "sha256:" + _sha256(common["mapping"]),
        "mapping binding mismatch",
    )
    for stage_name in STAGES:
        db_stage = database[stage_name]
        _check(
            db_stage["batch"]["status"] == "VERIFIED"
            and int(db_stage["batch"]["verified_count"]) == 2,
            f"database batch mismatch: {stage_name}",
        )
        _check(
            len(db_stage["batch_items"]) == 2
            and all(
                row["operation_result"] == "VERIFIED"
                and row["listing_effect_state"] == "VERIFIED"
                and row["action_clicked_at"]
                and row["readback_observed_at"]
                for row in db_stage["batch_items"]
            ),
            f"database item ledger mismatch: {stage_name}",
        )
        _check(
            len(db_stage["execution_attempts"]) == 2
            and all(
                row["status"] == "VERIFIED"
                and row["side_effect_state"] == "VERIFIED"
                for row in db_stage["execution_attempts"]
            ),
            f"database attempt ledger mismatch: {stage_name}",
        )

    return {
        "bundle_id": bundle_dir.name,
        "status": "VERIFIED",
        "internal_skus": sorted(expected_skus),
        "set_online_batch_id": stages["set_online"]["request"]["batch_id"],
        "set_online_execution_attempt_id": stages["set_online"]["request"][
            "execution_attempt_id"
        ],
        "set_offline_batch_id": stages["set_offline"]["request"]["batch_id"],
        "set_offline_execution_attempt_id": stages["set_offline"]["request"][
            "execution_attempt_id"
        ],
        "sanitized_sha256": {
            path.name: _sha256(path)
            for path in bundle_dir.iterdir()
            if path.is_file() and path.name != "validation_report.json"
        },
        "checks": {
            "two_batch_contract_binding_valid": True,
            "two_item_set_online_valid": True,
            "two_item_set_offline_valid": True,
            "strict_serial_trajectory_valid": True,
            "per_item_readback_valid": True,
            "database_ledger_valid": True,
            "receipt_and_ack_binding_valid": True,
            "redaction_valid": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    args = parser.parse_args()
    report = validate_multi_success_bundle(args.bundle)
    print(json.dumps({"ok": True, **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

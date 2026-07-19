from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from scripts import build_task11_human_report_payload as human_report


def test_task11_human_report_does_not_require_screenshot_evidence(tmp_path: Path) -> None:
    archive = tmp_path / "archive" / "ATTEMPT-T11"
    archive.mkdir(parents=True)
    queue = tmp_path / "queue"
    for name in ("inbox", "working", "results", "control"):
        (queue / name).mkdir(parents=True)
    (queue / "heartbeat.json").write_text(
        json.dumps({"status": "STOPPED"}), encoding="utf-8"
    )

    request = {
        "contract_version": 2,
        "execution_mode": "READ_ONLY",
        "task_id": "TASK-T11",
        "execution_attempt_id": "ATTEMPT-T11",
        "operation_id": "READ-T11",
        "read_batch_id": "READ-BATCH-T11",
        "capture_evidence": False,
    }
    result = {
        **request,
        "status": "READ_COMPLETED",
        "run_success_flag": True,
        "business_operation_completed": False,
        "side_effect_state": "NOT_STARTED",
        "platform_name": "蚂蚁花团供应商",
        "product_snapshots": [{
            "item_id": "ITEM-T11",
            "product_name": "艾莎",
            "grade": "C级",
            "platform_sku": "SKU-AISHA-C",
            "inventory": 20,
            "price": "6.00",
            "listing_status": "ONLINE",
            "item_status": "SUCCESS",
            "error_code": None,
            "row_identity": "parent-index:1",
            "evidence": [],
            "evidence_status": "SKIPPED",
        }],
        "total_count": 1,
        "processed_count": 1,
        "success_count": 1,
        "failed_count": 0,
        "skipped_count": 0,
        "manual_check_count": 0,
    }
    phase = {"phase": "RESULT_WRITTEN"}
    coverage = {
        "position_change_evidence": {
            "prior_sort_order": ["艾莎 C级"],
            "current_order_after_sort_change": ["艾莎 C级"],
        }
    }
    (archive / "ATTEMPT-T11.request.json").write_text(
        json.dumps(request, ensure_ascii=False), encoding="utf-8"
    )
    (archive / "ATTEMPT-T11.result.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8"
    )
    (archive / "ATTEMPT-T11.phase.json").write_text(
        json.dumps(phase, ensure_ascii=False), encoding="utf-8"
    )
    sort_acceptance = tmp_path / "sort.json"
    sort_acceptance.write_text(
        json.dumps(coverage, ensure_ascii=False), encoding="utf-8"
    )

    with patch.object(
        human_report,
        "_database_readback",
        return_value={"readback_passed": True},
    ):
        payload = human_report.build_payload(
            archive_dir=archive,
            runtime_db=tmp_path / "runtime.sqlite3",
            sort_acceptance=sort_acceptance,
            queue_root=queue,
        )

    assert payload["validation_passed"] is True
    assert payload["overall_status"] == "PASSED"
    assert payload["evidence_policy"]["required_for_success"] is False
    assert payload["evidence_policy"]["present_item_count"] == 0
    assert payload["evidence_policy"]["diagnostic_validation_applied"] is False
    assert payload["evidence_policy"]["diagnostic_validation_passed"] is True

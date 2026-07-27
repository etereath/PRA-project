from __future__ import annotations

import importlib.util
from pathlib import Path


WORKER_PATH = (
    Path(__file__).resolve().parents[1]
    / "shadowbot"
    / "test2"
    / "shadowbot_queue_worker.py"
)


def _load_worker_module():
    spec = importlib.util.spec_from_file_location("task13_shadowbot_worker", WORKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request_item(index: int) -> dict[str, object]:
    return {
        "source_task_id": f"TASK-T13-RECOVERY-00{index}",
        "operation_id": f"OPERATION-T13-RECOVERY-00{index}",
        "item_execution_attempt_id": f"ITEM-ATTEMPT-T13-RECOVERY-00{index}",
        "internal_sku": f"AISHA-{index}-60-Z",
        "item_payload_sha256": "sha256:" + str(index) * 64,
    }


def _phase_state(
    request_item: dict[str, object],
    *,
    detail_clicked: bool,
) -> dict[str, object]:
    return {
        **request_item,
        "operation_result": "NOT_ATTEMPTED",
        "detail_effect_state": "VERIFIED" if detail_clicked else "NOT_STARTED",
        "listing_effect_state": "NOT_STARTED",
        "detail_save_clicked": detail_clicked,
        "action_confirm_clicked": False,
    }


def test_worker_failure_result_preserves_multi_item_interruption_matrix() -> None:
    worker = _load_worker_module()
    first = _request_item(1)
    second = _request_item(2)
    request = {
        "contract_version": 5,
        "action_type": "set_online",
        "batch_id": "BATCH-T13-RECOVERY-001",
        "execution_attempt_id": "ATTEMPT-T13-RECOVERY-001",
        "manifest_sha256": "sha256:" + "a" * 64,
        "instruction_hash": "sha256:" + "b" * 64,
        "items": [first, second],
    }
    phase = {
        "phase": "ACTION_INTENT_RECORDED",
        "current_item": second,
        "detail_effect_state": "VERIFIED",
        "listing_effect_state": "NOT_STARTED",
        "item_states": [
            _phase_state(first, detail_clicked=True),
            _phase_state(second, detail_clicked=True),
        ],
    }

    result = worker._v5_failed_result(
        request,
        "c" * 64,
        worker_id="worker:test",
        error_code="WORKER_INTERRUPTED",
        error_message="受控中断",
        phase_data=phase,
    )

    assert [item["operation_result"] for item in result["items"]] == [
        "PARTIALLY_APPLIED",
        "NEEDS_RECONCILIATION",
    ]
    assert result["counts"]["partial_effect_count"] == 1
    assert result["counts"]["unknown_count"] == 1
    assert result["batch_status"] == "UNKNOWN"
    assert result["side_effect_state"] == "UNKNOWN"


def test_worker_failure_preserves_verified_then_unknown_then_not_attempted() -> None:
    worker = _load_worker_module()
    first = _request_item(1)
    second = _request_item(2)
    third = _request_item(3)
    first_state = {
        **_phase_state(first, detail_clicked=False),
        "operation_result": "VERIFIED",
        "listing_effect_state": "VERIFIED",
        "action_confirm_clicked": True,
        "action_clicked_at": "2026-07-26T12:00:01+00:00",
        "readback_observed_at": "2026-07-26T12:00:02+00:00",
    }
    second_state = {
        **_phase_state(second, detail_clicked=False),
        "listing_effect_state": "UNKNOWN",
        "action_confirm_clicked": True,
        "action_clicked_at": "2026-07-26T12:00:03+00:00",
    }
    request = {
        "contract_version": 5,
        "action_type": "set_offline",
        "batch_id": "BATCH-T13-RECOVERY-002",
        "execution_attempt_id": "ATTEMPT-T13-RECOVERY-002",
        "manifest_sha256": "sha256:" + "a" * 64,
        "instruction_hash": "sha256:" + "b" * 64,
        "items": [first, second, third],
    }
    phase = {
        "phase": "ACTION_CLICKED",
        "current_item": second,
        "detail_effect_state": "NOT_APPLIED",
        "listing_effect_state": "UNKNOWN",
        "item_states": [
            first_state,
            second_state,
            _phase_state(third, detail_clicked=False),
        ],
    }

    result = worker._v5_failed_result(
        request,
        "c" * 64,
        worker_id="worker:test",
        error_code="CONTROLLED_AFTER_ACTION_CLICK_UNKNOWN",
        error_message="受控故障",
        phase_data=phase,
    )

    assert [item["operation_result"] for item in result["items"]] == [
        "VERIFIED",
        "NEEDS_RECONCILIATION",
        "NOT_ATTEMPTED",
    ]
    assert result["counts"]["verified_count"] == 1
    assert result["counts"]["unknown_count"] == 1
    assert result["counts"]["not_attempted_count"] == 1
    assert result["batch_status"] == "UNKNOWN"


def test_worker_v5_manifest_hash_is_order_invariant_for_multi_item_batch() -> None:
    worker = _load_worker_module()
    base = {
        "schema_version": worker.V5_REQUEST_SCHEMA_VERSION,
        "contract_version": worker.V5_CONTRACT_VERSION,
        "batch_id": "BATCH-T13-MANIFEST-ORDER-001",
        "action_type": "set_online",
        "execution_mode": "COMMIT",
        "platform_name": "蚂蚁花团供应商",
        "mapping_source_version": "sha256:" + "a" * 64,
        "scan_scope": "online_and_waiting",
    }
    first = {
        "item_id": "ITEM-A",
        "source_task_id": "TASK-A",
        "internal_sku": "AISHA-E-45-Z",
        "expected_product_name": "艾莎",
        "expected_grade": "E级",
        "action_type": "set_online",
        "expected_old_status": "offline",
        "target_status": "online",
        "target_price": "7.50",
        "target_inventory": 2,
        "task_expires_at": "2026-07-26T08:30:00+00:00",
        "item_payload_sha256": "sha256:" + "b" * 64,
        "operation_id": "OP-A",
        "write_identity_key": "蚂蚁花团供应商|sku:AISHA-E-45-Z",
        "page_identity_key": "蚂蚁花团供应商|name:艾莎|grade:E",
    }
    second = {
        "item_id": "ITEM-B",
        "source_task_id": "TASK-B",
        "internal_sku": "CAPPUCCINO-E-45-Z",
        "expected_product_name": "卡布奇诺",
        "expected_grade": "E级",
        "action_type": "set_online",
        "expected_old_status": "offline",
        "target_status": "online",
        "target_price": "8.00",
        "target_inventory": 1,
        "task_expires_at": "2026-07-26T08:30:00+00:00",
        "item_payload_sha256": "sha256:" + "c" * 64,
        "operation_id": "OP-B",
        "write_identity_key": "蚂蚁花团供应商|sku:CAPPUCCINO-E-45-Z",
        "page_identity_key": "蚂蚁花团供应商|name:卡布奇诺|grade:E",
    }

    forward = worker._v5_manifest_hash({**base, "items": [first, second]})
    reverse = worker._v5_manifest_hash({**base, "items": [second, first]})

    assert reverse == forward

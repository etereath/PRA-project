from __future__ import annotations

import importlib.util
from pathlib import Path

from app.exceptions import ValidationError
from app.services.shadowbot_commit_batch import (
    build_commit_manifest,
    build_commit_request,
    compute_instruction_hash,
    required_development_confirmation,
    validate_request,
)


def _load_worker_module():
    path = Path("shadowbot/test2/shadowbot_queue_worker.py")
    spec = importlib.util.spec_from_file_location("shadowbot_queue_worker_for_fault_gate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_v4_request(*, profile: str = "development") -> dict[str, object]:
    batch_id = "BATCH-FAULT-GATE-0001"
    manifest = build_commit_manifest(
        batch_id=batch_id,
        task_items=[
            {
                "source_task_id": "TASK-FAULT-0001",
                "internal_sku": "AISHA-C-55-Z",
                "expected_old_price": "12.50",
                "target_price": "13.00",
            }
        ],
        identity_mapping={
            "AISHA-C-55-Z": {
                "expected_product_name": "艾莎",
                "expected_grade": "C",
            }
        },
        platform_name="蚂蚁花团供应商",
    )
    confirmation_text = (
        required_development_confirmation(batch_id, 1)
        if profile == "development"
        else ""
    )
    request = build_commit_request(
        manifest,
        execution_profile=profile,
        batch_task_id="BATCH-TASK-FAULT-0001",
        operation_id="BATCH-OP-FAULT-0001",
        execution_attempt_id="ATTEMPT-FAULT-GATE-0001",
        applet_uri="weixin://dl/business/?t=test",
        confirmation_text=confirmation_text,
        confirmed_by="test" if confirmation_text else "",
    )
    request["fault_injection"] = "AFTER_SUBMIT_CLICK_UNKNOWN"
    request["instruction_hash"] = compute_instruction_hash(request)
    return request


def test_queue_worker_rejects_fault_injection_by_default(tmp_path):
    worker_module = _load_worker_module()
    worker = worker_module.QueueWorker(
        {"queue_dir": str(tmp_path), "poll_seconds": 1, "max_hours": 1, "max_tasks": 1, "heartbeat_seconds": 5}
    )
    request = _valid_v4_request()

    try:
        worker._validate_request(request)
    except ValueError as exc:
        assert "UNSAFE_TEST_PARAMETER_REJECTED" in str(exc)
    else:
        raise AssertionError("fault injection should be rejected by default")


def test_queue_worker_allows_development_v4_fault_only_when_configured(tmp_path):
    worker_module = _load_worker_module()
    worker = worker_module.QueueWorker(
        {
            "queue_dir": str(tmp_path),
            "poll_seconds": 1,
            "max_hours": 1,
            "max_tasks": 1,
            "heartbeat_seconds": 5,
            "allow_fault_injection": True,
        }
    )
    request = _valid_v4_request()

    worker._validate_request(request)


def test_queue_worker_rejects_production_fault_even_when_configured(tmp_path):
    worker_module = _load_worker_module()
    worker = worker_module.QueueWorker(
        {
            "queue_dir": str(tmp_path),
            "poll_seconds": 1,
            "max_hours": 1,
            "max_tasks": 1,
            "heartbeat_seconds": 5,
            "allow_fault_injection": True,
        }
    )
    request = _valid_v4_request(profile="production")

    try:
        worker._validate_request(request)
    except ValueError as exc:
        assert "UNSAFE_TEST_PARAMETER_REJECTED" in str(exc)
    else:
        raise AssertionError("production fault injection must be rejected")


def test_app_contract_rejects_production_fault_injection():
    request = _valid_v4_request(profile="production")

    try:
        validate_request(request)
    except ValidationError as exc:
        assert "正式运行合同不得携带故障注入字段" in str(exc)
    else:
        raise AssertionError("production contract fault injection must be rejected")


def test_queue_worker_allows_reconcile_without_platform_sku(tmp_path):
    worker_module = _load_worker_module()
    worker = worker_module.QueueWorker(
        {
            "queue_dir": str(tmp_path),
            "poll_seconds": 1,
            "max_hours": 1,
            "max_tasks": 1,
            "heartbeat_seconds": 5,
        }
    )
    request = {
        "schema_version": "shadowbot-request-1.0",
        "task_id": "TASK-RECONCILE-0001",
        "operation_id": "OP-RECONCILE-0001",
        "execution_attempt_id": "RECONCILE-ATTEMPT-0001",
        "execution_mode": "RECONCILE",
        "platform_name": "蚂蚁花团供应商",
        "platform_sku": "",
        "product_keyword": "B艾莎",
        "expected_product_name": "艾莎",
        "expected_grade": "B",
        "expected_old_price": "10.20",
        "target_price": "10.30",
        "expires_at": "2099-01-01T00:00:00+00:00",
    }
    request["instruction_hash"] = worker_module._instruction_hash(request)

    worker._validate_request(request)

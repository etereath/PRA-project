from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_worker_module():
    path = Path("shadowbot/test2/shadowbot_queue_worker.py")
    spec = importlib.util.spec_from_file_location("shadowbot_queue_worker_for_fault_gate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_request() -> dict[str, object]:
    worker_module = _load_worker_module()
    request = {
        "task_id": "TASK-1",
        "operation_id": "OP-1",
        "execution_attempt_id": "ATTEMPT-1",
        "execution_mode": "COMMIT",
        "platform_name": "蚂蚁花团供应商",
        "platform_sku": "SKU-AISHA-C",
        "product_keyword": "C级艾莎",
        "expected_product_name": "艾莎",
        "expected_grade": "C级",
        "expected_spec": "",
        "spec_verification_required": False,
        "expected_old_price": "12.50",
        "target_price": "13.00",
        "applet_uri": "",
        "expires_at": "2999-01-01T00:00:00+00:00",
    }
    request["instruction_hash"] = worker_module._instruction_hash(request)
    return request


def test_queue_worker_rejects_fault_injection_by_default(tmp_path):
    worker_module = _load_worker_module()
    worker = worker_module.QueueWorker(
        {"queue_dir": str(tmp_path), "poll_seconds": 1, "max_hours": 1, "max_tasks": 1, "heartbeat_seconds": 5}
    )
    request = _valid_request()
    request["fault_injection"] = "AFTER_SUBMIT_CLICK_UNKNOWN"

    try:
        worker._validate_request(request)
    except ValueError as exc:
        assert "UNSAFE_TEST_PARAMETER_REJECTED" in str(exc)
    else:
        raise AssertionError("fault injection should be rejected by default")


def test_queue_worker_allows_fault_injection_only_when_configured(tmp_path):
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
    request = _valid_request()
    request["fault_injection"] = "AFTER_SUBMIT_CLICK_UNKNOWN"

    worker._validate_request(request)

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.adapters.mayi_huatuan_order import OrderReadOnlyRequest
from app.services.order_observation import OrderObservationError
from app.services.shadowbot_order_read import (
    ShadowBotFileQueueOrderTransport,
    ShadowBotOrderPageReader,
    validate_order_scan_result,
)
from app.shadowbot_contract_primitives import (
    ORDER_SCAN_CONTRACT_VERSION,
    ORDER_SCAN_REQUEST_SCHEMA_VERSION,
    ORDER_SCAN_RESULT_SCHEMA_VERSION,
    normalize_order_scan_request,
    order_scan_instruction_hash,
)
from shadowbot.test2.shadowbot_queue_worker import (
    _v6_failed_result,
    _v6_validate_request,
)


NOW = datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc)


def _capture() -> dict[str, Any]:
    return {
        "selected_platform_trade_date": "2026-07-30",
        "scan_started_at": "2026-07-31T01:59:00+00:00",
        "scan_completed_at": "2026-07-31T02:00:00+00:00",
        "loading_completed": True,
        "scroll_completed": True,
        "no_more_marker_visible": True,
        "trusted_empty_marker_visible": False,
        "page_count": 2,
        "rows": [
            {
                "order_created_at": "2026-07-30 17:59:59",
                "platform_product_name": "合成玫瑰",
                "grade": "A",
                "order_qty": "2",
                "order_transaction_amount": "19.80",
                "observed_at": "2026-07-31T01:59:30+00:00",
            }
        ],
        "unavailable_code": "",
        "failure_code": "",
        "failure_message": "",
    }


def _request(attempt_id: str = "ORDER-READ-TEST-0001") -> dict[str, Any]:
    created_at = datetime.now(timezone.utc)
    payload = {
        "schema_version": ORDER_SCAN_REQUEST_SCHEMA_VERSION,
        "contract_version": ORDER_SCAN_CONTRACT_VERSION,
        "execution_mode": "READ_ONLY",
        "automation_run_id": "RUN-ORDER-0001",
        "observation_batch_id": "ORDER-BATCH-0001",
        "execution_attempt_id": attempt_id,
        "platform_name": "蚂蚁花团供应商",
        "requested_platform_trade_date": "2026-07-30",
        "window_title": "蚂蚁花团供应商",
        "applet_uri": "",
        "element_timeout_seconds": 15,
        "applet_launch_timeout_seconds": 20,
        "limits": {
            "max_rows": 500,
            "max_scrolls": 100,
            "max_seconds": 300,
        },
        "created_at": created_at.isoformat(),
        "expires_at": (created_at + timedelta(minutes=10)).isoformat(),
    }
    payload.update(normalize_order_scan_request(payload))
    payload["instruction_hash"] = order_scan_instruction_hash(payload)
    return payload


def _result(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": ORDER_SCAN_RESULT_SCHEMA_VERSION,
        "contract_version": ORDER_SCAN_CONTRACT_VERSION,
        "execution_attempt_id": request["execution_attempt_id"],
        "automation_run_id": request["automation_run_id"],
        "observation_batch_id": request["observation_batch_id"],
        "execution_mode": "READ_ONLY",
        "platform_name": request["platform_name"],
        "requested_platform_trade_date": (
            request["requested_platform_trade_date"]
        ),
        "instruction_hash": request["instruction_hash"],
        "business_operation_completed": False,
        "side_effect_state": "NOT_STARTED",
        "status": "SUCCESS",
        "capture": _capture(),
    }


class _RecordingTransport:
    def __init__(self) -> None:
        self.request: dict[str, Any] | None = None

    def execute_order_read(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        self.request = dict(request)
        return _result(self.request)


def test_reader_builds_v6_read_only_request_and_parses_capture() -> None:
    transport = _RecordingTransport()
    reader = ShadowBotOrderPageReader(
        transport,
        clock=lambda: NOW,
        attempt_id_factory=lambda: "ORDER-READ-TEST-0001",
    )

    capture = reader.read_orders_read_only(
        OrderReadOnlyRequest(
            execution_mode="READ_ONLY",
            automation_run_id="RUN-ORDER-0001",
            observation_batch_id="ORDER-BATCH-0001",
            platform_name="蚂蚁花团供应商",
            requested_platform_trade_date=date(2026, 7, 30),
        )
    )

    assert transport.request is not None
    assert transport.request["contract_version"] == 6
    assert transport.request["execution_mode"] == "READ_ONLY"
    assert (
        transport.request["instruction_hash"]
        == order_scan_instruction_hash(transport.request)
    )
    assert capture.rows[0].platform_product_name == "合成玫瑰"


def test_result_rejects_pii_and_platform_write_side_effects() -> None:
    request = _request()
    result = _result(request)
    result["request_file_sha256"] = ""
    result["capture"]["rows"][0]["buyer_phone"] = "synthetic"
    with pytest.raises(OrderObservationError, match="forbidden"):
        validate_order_scan_result(request, result)

    result = _result(request)
    result["business_operation_completed"] = True
    with pytest.raises(OrderObservationError, match="write side effect"):
        validate_order_scan_result(request, result)

    result = _result(request)
    result["capture"]["rows"][0]["buyerPhone"] = "synthetic"
    with pytest.raises(OrderObservationError, match="forbidden"):
        validate_order_scan_result(request, result)

    result = _result(request)
    result["订单号"] = "synthetic"
    with pytest.raises(OrderObservationError, match="forbidden"):
        validate_order_scan_result(request, result)


def test_worker_validates_v6_hash_and_builds_safe_failure() -> None:
    request = _request()
    _v6_validate_request(request)
    failed = _v6_failed_result(
        request,
        "a" * 64,
        worker_id="WORKER-TEST",
        error_code="ORDER_TEST_FAILURE",
        error_message="synthetic failure",
    )

    assert failed["side_effect_state"] == "NOT_STARTED"
    assert failed["business_operation_completed"] is False
    assert failed["capture"]["rows"] == []
    assert failed["capture"]["failure_code"] == "ORDER_TEST_FAILURE"


def test_file_queue_transport_publishes_reads_and_archives_after_ack(
    tmp_path: Path,
) -> None:
    queue_dir = tmp_path / "queue"
    request = _request()
    result = _result(request)
    published = False

    def publish_result(_: float) -> None:
        nonlocal published
        if published:
            return
        ready = (
            queue_dir
            / "inbox"
            / f"{request['execution_attempt_id']}.ready.json"
        )
        if not ready.exists():
            return
        result_dir = queue_dir / "results"
        result_dir.mkdir(parents=True, exist_ok=True)
        result_path = result_dir / (
            f"{request['execution_attempt_id']}.result.json"
        )
        content = json.dumps(
            {
                **result,
                "request_file_sha256": "sha256:"
                + hashlib.sha256(ready.read_bytes()).hexdigest(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        checksum = hashlib.sha256(content).hexdigest()
        result_path.with_suffix(
            result_path.suffix + ".sha256"
        ).write_text(checksum + "\n", encoding="ascii")
        result_path.write_bytes(content)
        published = True

    transport = ShadowBotFileQueueOrderTransport(
        queue_dir,
        timeout_seconds=1,
        poll_interval_seconds=0.01,
        require_fresh_heartbeat=False,
        sleeper=publish_result,
    )

    returned = transport.execute_order_read(request)
    assert returned["capture"]["rows"][0]["order_qty"] == "2"
    transport.acknowledge_last_result()

    archive = queue_dir / "archive" / request["execution_attempt_id"]
    assert (archive / f"{request['execution_attempt_id']}.ready.json").exists()
    assert (
        archive / f"{request['execution_attempt_id']}.result.json"
    ).exists()
    assert not list((queue_dir / "results").glob("*.result.json"))


def test_file_queue_transport_requires_fresh_running_heartbeat(
    tmp_path: Path,
) -> None:
    transport = ShadowBotFileQueueOrderTransport(
        tmp_path / "queue",
        timeout_seconds=1,
        clock=lambda: NOW,
    )

    with pytest.raises(OrderObservationError, match="heartbeat"):
        transport.execute_order_read(_request())


def test_order_vertical_slice_has_no_order_id_selector_or_write_action() -> None:
    source = (
        Path("shadowbot/test2/vertical_slice_read_price.py")
        .read_text(encoding="utf-8")
    )

    assert "订单管理_订单1_单号" not in source
    assert "def _run_order_scan_v6" in source
    assert '"side_effect_state": "NOT_STARTED"' in source

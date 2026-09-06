from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

from app.adapters.mayi_huatuan_order import MAYI_HUATUAN_PLATFORM
from app.automation_models import AutomationJob, AutomationRunOutcome
from app.enums import AutomationRunStatus
from app.repositories.automation_repository import AutomationRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import (
    CHILD_ONLY,
    FULL_MARKET_SCAN,
    INTERVAL_MINUTES,
    ORDER_SCAN,
)
from app.services.operational_time import OperationalTimeService
from app.services.shadowbot_executor import ShadowBotFileQueueRunner
from app.services.shadowbot_order_read import validate_order_scan_result
from app.services.shadowbot_queue import (
    ShadowBotQueueWatchdog,
    ShadowBotResultImporter,
)
from app.shadowbot_contract_primitives import (
    ORDER_SCAN_CONTRACT_VERSION,
    ORDER_SCAN_REQUEST_SCHEMA_VERSION,
    build_order_scan_failure_result,
    normalize_order_scan_request,
    order_scan_instruction_hash,
)


NOW = datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc)


def _request(
    run_id: str,
    *,
    target_trade_date: str = "2026-07-31",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": ORDER_SCAN_REQUEST_SCHEMA_VERSION,
        "contract_version": ORDER_SCAN_CONTRACT_VERSION,
        "execution_mode": "READ_ONLY",
        "automation_run_id": run_id,
        "observation_batch_id": f"ORDER-BATCH-{run_id}",
        "execution_attempt_id": "ORDER-READ-SYNTHETIC-QUEUE",
        "platform_name": MAYI_HUATUAN_PLATFORM,
        "requested_platform_trade_date": target_trade_date,
        "window_title": MAYI_HUATUAN_PLATFORM,
        "applet_uri": "",
        "element_timeout_seconds": 15,
        "applet_launch_timeout_seconds": 20,
        "limits": {
            "max_rows": 500,
            "max_scrolls": 100,
            "max_seconds": 300,
        },
        "created_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
    }
    payload.update(normalize_order_scan_request(payload))
    payload["instruction_hash"] = order_scan_instruction_hash(payload)
    return payload


def _running_order_scan(
    runtime: SQLiteRuntimeRepository,
    *,
    target_trade_date: date = date(2026, 7, 31),
) -> str:
    repository = AutomationRepository(runtime)
    parent_job = repository.upsert_job(
        AutomationJob(
            job_id="FULL-SYNTHETIC",
            job_type=FULL_MARKET_SCAN,
            display_name=FULL_MARKET_SCAN,
            enabled=True,
            schedule_kind=INTERVAL_MINUTES,
            schedule_expression="60",
            priority=50,
            config={
                "platform_name": MAYI_HUATUAN_PLATFORM,
                "catchup_policy": "LATEST_ONLY",
            },
        ),
        now=NOW,
    )
    child_job = repository.upsert_job(
        AutomationJob(
            job_id="ORDER-SYNTHETIC",
            job_type=ORDER_SCAN,
            display_name=ORDER_SCAN,
            enabled=False,
            schedule_kind=CHILD_ONLY,
            schedule_expression="-",
            priority=50,
            config={
                "platform_name": MAYI_HUATUAN_PLATFORM,
                "catchup_policy": "LATEST_ONLY",
            },
        ),
        now=NOW,
    )
    parent = repository.ensure_run(
        job=parent_job,
        scheduled_for=NOW,
        time_context=OperationalTimeService().classify(NOW),
        initial_status=AutomationRunStatus.SCHEDULED,
        now=NOW,
    )[0]
    parent_claim = repository.claim_run(
        run_id=parent.run_id,
        owner_token="synthetic-parent-owner",
        now=NOW,
        lease_seconds=600,
    )
    assert parent_claim is not None
    child, _ = repository.ensure_child_run_fenced(
        parent_claim,
        child_job,
        relation_type="ORDER_SCAN_CHILD",
        now=NOW,
    )
    assert repository.finish_run(
        parent_claim,
        AutomationRunOutcome(status=AutomationRunStatus.SUCCESS),
        now=NOW + timedelta(seconds=1),
    )
    child_claim = repository.claim_run(
        run_id=child.run_id,
        owner_token="synthetic-order-owner",
        now=NOW + timedelta(seconds=2),
        lease_seconds=600,
    )
    assert child_claim is not None
    repository.bind_order_scan_target_trade_date(
        child_claim,
        target_trade_date=target_trade_date,
        now=NOW + timedelta(seconds=3),
    )
    return child.run_id


def test_watchdog_accepts_v6_ready_request_bound_to_running_order_scan(
    tmp_path,
) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    run_id = _running_order_scan(runtime)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    (queue_dir / "heartbeat.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "worker_id": "synthetic-worker",
                "updated_at": NOW.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    request = _request(run_id)
    ShadowBotFileQueueRunner(queue_dir).start(request)

    watchdog = ShadowBotQueueWatchdog(
        queue_dir,
        stale_seconds=30,
        repository=runtime,
    )
    events = watchdog.inspect(now=NOW)

    assert events == [
        {
            "status": "READY_REQUEST_VALIDATED",
            "contract_version": ORDER_SCAN_CONTRACT_VERSION,
            "execution_attempt_id": "ORDER-READ-SYNTHETIC-QUEUE",
            "automation_run_id": run_id,
            "requested_platform_trade_date": "2026-07-31",
        }
    ]
    assert watchdog.inspect(now=NOW) == []
    assert (
        queue_dir
        / "inbox"
        / "ORDER-READ-SYNTHETIC-QUEUE.ready.json"
    ).exists()


def test_watchdog_accepts_only_the_frozen_historical_trade_date(
    tmp_path,
) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    run_id = _running_order_scan(
        runtime,
        target_trade_date=date(2026, 7, 30),
    )
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    historical = _request(run_id, target_trade_date="2026-07-30")
    historical["execution_attempt_id"] = "ORDER-READ-HISTORICAL"
    historical["instruction_hash"] = order_scan_instruction_hash(historical)
    ShadowBotFileQueueRunner(queue_dir).start(historical)

    watchdog = ShadowBotQueueWatchdog(
        queue_dir,
        stale_seconds=30,
        repository=runtime,
    )
    events = watchdog.inspect(now=NOW)

    assert events == [
        {
            "status": "READY_REQUEST_VALIDATED",
            "contract_version": ORDER_SCAN_CONTRACT_VERSION,
            "execution_attempt_id": "ORDER-READ-HISTORICAL",
            "automation_run_id": run_id,
            "requested_platform_trade_date": "2026-07-30",
        }
    ]
    assert watchdog.inspect(now=NOW) == []
    assert (
        queue_dir / "inbox" / "ORDER-READ-HISTORICAL.ready.json"
    ).exists()

    wrong = _request(run_id, target_trade_date="2026-07-31")
    wrong["execution_attempt_id"] = "ORDER-READ-WRONG-DATE"
    wrong["instruction_hash"] = order_scan_instruction_hash(wrong)
    ShadowBotFileQueueRunner(queue_dir).start(wrong)
    events = watchdog.inspect(now=NOW)

    assert any(
        event.get("error_code") == "ORPHAN_READY_REQUEST"
        for event in events
    )


def test_generic_result_importer_defers_v6_to_order_importer(
    tmp_path,
) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    queue_dir = tmp_path / "queue"
    request = _request("AUTO-RUN-SYNTHETIC")
    request_sha256 = hashlib.sha256(b"synthetic-request").hexdigest()
    result = build_order_scan_failure_result(
        request,
        request_sha256,
        worker_id="synthetic-worker",
        error_code="SYNTHETIC_FAILURE",
        error_message="synthetic failure",
        observed_at=NOW.isoformat(),
    )
    result_path = (
        queue_dir
        / "results"
        / "ORDER-READ-SYNTHETIC-QUEUE.result.json"
    )
    result_path.parent.mkdir(parents=True)
    result_bytes = (
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    result_path.write_bytes(result_bytes)
    result_path.with_suffix(result_path.suffix + ".sha256").write_text(
        hashlib.sha256(result_bytes).hexdigest() + "\n",
        encoding="ascii",
    )

    events = ShadowBotResultImporter(
        runtime,
        Mock(),
        queue_dir,
    ).import_available()

    assert events == [
        {
            "status": "DEFERRED",
            "error_code": "ORDER_RESULT_REQUIRES_AUTOMATION_IMPORTER",
            "execution_attempt_id": "ORDER-READ-SYNTHETIC-QUEUE",
            "path": str(result_path),
        }
    ]
    assert result_path.exists()


def test_watchdog_v6_recovery_is_read_only_and_contract_valid(
    tmp_path,
) -> None:
    runtime = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    runtime.init_schema()
    queue_dir = tmp_path / "queue"
    request = _request("AUTO-RUN-SYNTHETIC")
    request_sha256 = hashlib.sha256(b"synthetic-request").hexdigest()
    watchdog = ShadowBotQueueWatchdog(
        queue_dir,
        repository=runtime,
    )

    event = watchdog._write_recovery_result(  # noqa: SLF001
        request,
        {
            "request_file_sha256": "sha256:" + request_sha256,
            "worker_id": "watchdog:synthetic",
        },
        phase="CLAIMED",
    )

    result_path = queue_dir / "results" / (
        "ORDER-READ-SYNTHETIC-QUEUE.result.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    capture = validate_order_scan_result(request, result)
    assert event["status"] == "RECOVERY_RESULT_WRITTEN"
    assert result["side_effect_state"] == "NOT_STARTED"
    assert result["business_operation_completed"] is False
    assert capture.failure_code == "WORKER_INTERRUPTED"

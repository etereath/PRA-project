from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_executor import ShadowBotFileQueueRunner
from app.services.shadowbot_queue import ShadowBotQueueWatchdog, ShadowBotResultImporter
from scripts.prepare_task12_source_read import prepare_task12_source_read


def test_prepare_task12_source_read_creates_v2_queue_request_and_runtime_binding(tmp_path):
    source_request = tmp_path / "任务11来源请求.json"
    source_request.write_text(
        json.dumps(
            {
                "applet_uri": "weixin://launchapplet/?app_id=test",
                "window_title": "蚂蚁花团供应商",
                "platform_name": "蚂蚁花团供应商",
                "limits": {"max_pages": 20, "max_scrolls": 100, "max_seconds": 300},
                "products": [
                    {
                        "item_id": "OLD-ITEM-1",
                        "expected_product_name": "艾莎",
                        "expected_grade": "B级",
                        "platform": "蚂蚁花团供应商",
                        "platform_sku": "SKU-AISHA-B",
                    },
                    {
                        "item_id": "OLD-ITEM-2",
                        "expected_product_name": "卡布奇诺",
                        "expected_grade": "C级",
                        "platform": "蚂蚁花团供应商",
                        "platform_sku": "SKU-CAPPUCCINO-C",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    runtime_db = tmp_path / "runtime.sqlite3"
    queue_dir = tmp_path / "queue"
    result = prepare_task12_source_read(
        runtime_db=runtime_db,
        queue_dir=queue_dir,
        source_request=source_request,
        read_batch_id="READ-BATCH-T12-TEST",
        task_id="TASK-T12-SOURCE-TEST",
        execution_attempt_id="ATTEMPT-T12-SOURCE-TEST",
        now=datetime(2026, 7, 21, 1, 2, 3, tzinfo=UTC),
    )

    request_path = queue_dir / "inbox" / "ATTEMPT-T12-SOURCE-TEST.ready.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert result["product_count"] == 2
    assert request["contract_version"] == 2
    assert request["execution_mode"] == "READ_ONLY"
    assert request["capture_evidence"] is False
    assert request["read_batch_id"] == "READ-BATCH-T12-TEST"
    assert [item["item_id"] for item in request["products"]] == [
        "ITEM-T12-SOURCE-01-20260721-010203",
        "ITEM-T12-SOURCE-02-20260721-010203",
    ]
    assert [item["expected_product_name"] for item in request["products"]] == ["艾莎", "卡布奇诺"]
    assert request["applet_uri"].startswith("weixin://")
    assert request["window_title"] == "蚂蚁花团供应商"

    repository = SQLiteRuntimeRepository(runtime_db)
    task = repository.get_task("TASK-T12-SOURCE-TEST")
    attempt = repository.get_shadowbot_execution_attempt("ATTEMPT-T12-SOURCE-TEST")
    assert task is not None
    assert attempt is not None
    assert attempt.request_file_sha256 == result["request_file_sha256"]
    assert request_path.with_suffix(request_path.suffix + ".sha256").is_file()


def test_watchdog_recovery_result_keeps_v2_batch_identity_and_imports_once(tmp_path):
    source_request = tmp_path / "任务11来源请求.json"
    source_request.write_text(
        json.dumps(
            {
                "applet_uri": "weixin://launchapplet/?app_id=test",
                "window_title": "蚂蚁花团供应商",
                "platform_name": "蚂蚁花团供应商",
                "products": [
                    {
                        "item_id": "OLD-ITEM-1",
                        "expected_product_name": "艾莎",
                        "expected_grade": "B级",
                        "platform": "蚂蚁花团供应商",
                        "platform_sku": "SKU-AISHA-B",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    runtime_db = tmp_path / "runtime.sqlite3"
    queue_dir = tmp_path / "queue"
    attempt_id = "ATTEMPT-T12-SOURCE-RECOVERY"
    prepare_task12_source_read(
        runtime_db=runtime_db,
        queue_dir=queue_dir,
        source_request=source_request,
        read_batch_id="READ-BATCH-T12-RECOVERY",
        task_id="TASK-T12-SOURCE-RECOVERY",
        execution_attempt_id=attempt_id,
        now=datetime(2026, 7, 21, 1, 2, 3, tzinfo=UTC),
    )
    ready_path = queue_dir / "inbox" / f"{attempt_id}.ready.json"
    ready_checksum = ready_path.with_suffix(ready_path.suffix + ".sha256")
    working_path = queue_dir / "working" / f"{attempt_id}.request.json"
    working_checksum = working_path.with_suffix(working_path.suffix + ".sha256")
    ready_path.replace(working_path)
    ready_checksum.replace(working_checksum)
    old_time = datetime.now(UTC) - timedelta(minutes=5)
    (queue_dir / "working" / f"{attempt_id}.phase.json").write_text(
        json.dumps(
            {
                "execution_attempt_id": attempt_id,
                "execution_mode": "READ_ONLY",
                "phase": "UI_STARTED",
                "side_effect_state": "NOT_STARTED",
                "updated_at": old_time.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "heartbeat.json").write_text(
        json.dumps({"status": "RUNNING", "updated_at": old_time.isoformat()}),
        encoding="utf-8",
    )

    watchdog = ShadowBotQueueWatchdog(queue_dir, stale_seconds=30)
    events = watchdog.inspect(now=datetime.now(UTC))
    assert events[0]["status"] == "RECOVERY_RESULT_WRITTEN"
    result_path = queue_dir / "results" / f"{attempt_id}.result.json"
    recovery = json.loads(result_path.read_text(encoding="utf-8"))
    assert recovery["contract_version"] == 2
    assert recovery["read_batch_id"] == "READ-BATCH-T12-RECOVERY"
    assert recovery["status"] == "FAILED"
    assert recovery["product_snapshots"] == []

    repository = SQLiteRuntimeRepository(runtime_db)
    importer = ShadowBotResultImporter(
        repository,
        ShadowBotFileQueueRunner(queue_dir),
        queue_dir,
    )
    imported = importer.import_available()
    assert imported[0]["status"] == "IMPORTED"
    assert not list((queue_dir / "working").glob(f"{attempt_id}*"))
    assert not list((queue_dir / "results").glob(f"{attempt_id}*"))
    assert (queue_dir / "archive" / attempt_id / f"{attempt_id}.result.json").is_file()

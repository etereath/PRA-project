from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

import pytest

from app.enums import TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.models import Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_commit_pipeline import (
    _task_time_as_utc,
    build_task_commit_manifest,
    import_task_commit_result,
    prepare_task_commit_batch,
    publish_task_commit_batch,
)
from app.services.shadowbot_executor import (
    ShadowBotFileQueueRunner,
    ShadowBotStartBoundaryError,
    ShadowBotStartResult,
)
from app.services.shadowbot_queue import ShadowBotQueueWatchdog, ShadowBotResultImporter
from shadowbot.test2.shadowbot_queue_worker import _v4_validate_request
from scripts.patch_shadowbot_queue_request_fault import main as patch_request_fault_main


MAPPING_PATH = "shadowbot/test2/product_identity_mapping.json"
OBSERVED_AT = "2026-07-22T04:59:00+00:00"


def _complete_result_item(item):
    completed = {
        **item,
        "side_effect_state": "NOT_STARTED",
        "preflight_observed_at": (
            OBSERVED_AT if item.get("preflight_row") is not None else None
        ),
        "submit_intent_at": None,
        "submit_clicked_at": None,
        "readback_observed_at": None,
    }
    status = str(completed.get("status") or "")
    if status == "VERIFIED":
        completed.update(
            {
                "side_effect_state": "VERIFIED",
                "submit_intent_at": OBSERVED_AT,
                "submit_clicked_at": OBSERVED_AT,
                "readback_observed_at": OBSERVED_AT,
            }
        )
    elif status == "UNKNOWN":
        completed.update(
            {
                "side_effect_state": "UNKNOWN",
                "submit_intent_at": OBSERVED_AT,
                "submit_clicked_at": OBSERVED_AT,
            }
        )
    elif status == "NOT_APPLIED" and completed.get("submit_attempted") is True:
        completed.update(
            {
                "side_effect_state": "NOT_APPLIED",
                "submit_intent_at": OBSERVED_AT,
                "submit_clicked_at": OBSERVED_AT,
                "readback_observed_at": OBSERVED_AT,
            }
        )
    return completed


class CapturingRunner:
    def __init__(self) -> None:
        self.requests = []

    def start(self, payload):
        self.requests.append(payload)
        return ShadowBotStartResult(
            shadowbot_run_id="filequeue:" + payload["execution_attempt_id"],
            raw_output={"instruction_hash": payload["instruction_hash"]},
        )


class BoundaryRunner:
    def __init__(self, *, published):
        self.published = published

    def start(self, payload):
        raise ShadowBotStartBoundaryError(
            "controlled publish boundary",
            published=self.published,
            raw_output={"execution_attempt_id": payload["execution_attempt_id"]},
        )


def _task(task_id, sku, old_price, target_price):
    now = datetime.now(timezone.utc)
    return Task(
        task_id=task_id,
        internal_sku=sku,
        platform_name="蚂蚁花团供应商",
        action_type=TaskActionType.UPDATE_PRICE,
        priority=10,
        task_status=TaskStatus.PENDING,
        created_at=now,
        expected_old_price=Decimal(old_price),
        target_price=Decimal(target_price),
        expires_at=now + timedelta(hours=1),
        scope_key=sku,
    )


def _seed_listing_status(repository):
    with closing(repository.connect_write()) as connection, connection:
        connection.executemany(
            """
            INSERT INTO listing_status(
                listing_status_id, platform_name, internal_sku, variety, grade,
                current_price, platform_stock_qty, sold_qty, online_status, source,
                updated_at, inventory_source, inventory_observed_at,
                inventory_source_attempt_id
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 0, 'online', 'test', ?, 'default', NULL, '')
            """,
            [
                ("LISTING-TEST-CAP-B", "蚂蚁花团供应商", "CAPPUCCINO-B-60-Z", "卡布奇诺", "B", "46.30", "2026-07-22T00:00:00+00:00"),
                ("LISTING-TEST-AISHA-B", "蚂蚁花团供应商", "SKU-002", "艾莎", "B", "26.30", "2026-07-22T00:00:00+00:00"),
            ],
        )


def _publish_file_queue_batch(repository, queue_dir, task_ids, batch_id):
    manifest = prepare_task_commit_batch(
        repository,
        task_ids=task_ids,
        mapping_path=Path(MAPPING_PATH),
        batch_id=batch_id,
        execution_profile="production",
    )
    runner = ShadowBotFileQueueRunner(queue_dir)
    request, _ = publish_task_commit_batch(
        repository,
        runner,
        manifest=manifest,
        execution_profile="production",
        applet_uri="weixin://dl/business/?t=test",
    )
    attempt_id = request["execution_attempt_id"]
    ready = queue_dir / "inbox" / f"{attempt_id}.ready.json"
    ready_checksum = ready.with_suffix(ready.suffix + ".sha256")
    working = queue_dir / "working" / f"{attempt_id}.request.json"
    working_checksum = working.with_suffix(working.suffix + ".sha256")
    os.replace(ready, working)
    os.replace(ready_checksum, working_checksum)
    return request, working, runner


def _write_v4_result(
    queue_dir,
    request,
    items,
    batch_status,
    *,
    page_snapshot=None,
):
    items = [_complete_result_item(item) for item in items]
    attempt_id = request["execution_attempt_id"]
    request_path = queue_dir / "working" / f"{attempt_id}.request.json"
    counts = {
        "total": len(items),
        "attempted": sum(bool(item["submit_attempted"]) for item in items),
        "verified": sum(item["status"] == "VERIFIED" for item in items),
        "not_applied": sum(item["status"] == "NOT_APPLIED" for item in items),
        "failed": sum(item["status"] == "FAILED" for item in items),
        "unknown": sum(item["status"] == "UNKNOWN" for item in items),
        "not_attempted": sum(item["status"] == "NOT_ATTEMPTED" for item in items),
    }
    result = {
        "schema_version": "shadowbot-commit-batch-result-1.1",
        "contract_version": 4,
        "result_id": "RESULT-" + attempt_id,
        "task_id": request["task_id"],
        "operation_id": request["operation_id"],
        "execution_attempt_id": attempt_id,
        "execution_mode": "COMMIT",
        "batch_id": request["batch_id"],
        "manifest_sha256": request["manifest_sha256"],
        "instruction_hash": request["instruction_hash"],
        "request_file_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
        "batch_status": batch_status,
        "status": batch_status,
        "run_success_flag": batch_status == "VERIFIED",
        "business_operation_completed": counts["attempted"] > 0,
        "side_effect_state": "VERIFIED" if counts["verified"] else "NOT_STARTED",
        "retryable": False,
        "error_code": "" if batch_status == "VERIFIED" else "TEST_BATCH_STOPPED",
        "error_message": "" if batch_status == "VERIFIED" else "test batch stopped",
        "items": items,
        "counts": counts,
        "ended_at": "2026-07-22T05:00:00+00:00",
    }
    if page_snapshot is not None:
        result["page_snapshot"] = page_snapshot
    result_path = queue_dir / "results" / f"{attempt_id}.result.json"
    result_bytes = (
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    result_path.write_bytes(result_bytes)
    result_path.with_suffix(result_path.suffix + ".sha256").write_text(
        hashlib.sha256(result_bytes).hexdigest() + "\n",
        encoding="ascii",
    )
    return result_path


def test_patch_v4_fault_requires_stopped_worker_and_updates_all_bindings(
    tmp_path,
    monkeypatch,
):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    repository.insert_tasks(
        [_task("TASK-FAULT-PATCH-0001", "CAPPUCCINO-B-60-Z", "46.30", "46.40")]
    )
    _seed_listing_status(repository)
    manifest = prepare_task_commit_batch(
        repository,
        task_ids=["TASK-FAULT-PATCH-0001"],
        mapping_path=Path(MAPPING_PATH),
        batch_id="BATCH-FAULT-PATCH-0001",
        execution_profile="development",
    )
    queue_dir = tmp_path / "queue"
    runner = ShadowBotFileQueueRunner(queue_dir)
    confirmation = manifest["development_confirmation_text"]
    request, _ = publish_task_commit_batch(
        repository,
        runner,
        manifest=manifest,
        execution_profile="development",
        applet_uri="weixin://dl/business/?t=test",
        confirmation_text=confirmation,
        confirmed_by="test",
    )
    (queue_dir / "heartbeat.json").write_text(
        '{"status":"STOPPED","updated_at":"2026-07-23T00:00:00+00:00"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "patch_shadowbot_queue_request_fault.py",
            "--queue-dir",
            str(queue_dir),
            "--execution-attempt-id",
            request["execution_attempt_id"],
            "--fault-injection",
            "AFTER_SUBMIT_CLICK_UNKNOWN",
            "--runtime-db",
            str(tmp_path / "runtime.sqlite3"),
        ],
    )

    assert patch_request_fault_main() == 0

    ready = queue_dir / "inbox" / f"{request['execution_attempt_id']}.ready.json"
    patched = json.loads(ready.read_text(encoding="utf-8"))
    assert patched["fault_injection"] == "AFTER_SUBMIT_CLICK_UNKNOWN"
    _v4_validate_request(patched)
    with repository.connect_read() as connection:
        batch = connection.execute(
            "SELECT instruction_hash FROM shadowbot_commit_batches WHERE batch_id = ?",
            (request["batch_id"],),
        ).fetchone()
        attempts = connection.execute(
            """
            SELECT instruction_hash, request_file_sha256
            FROM shadowbot_execution_attempts
            WHERE execution_attempt_id IN (
                SELECT item_execution_attempt_id
                FROM shadowbot_commit_batch_items
                WHERE batch_id = ?
            )
            """,
            (request["batch_id"],),
        ).fetchall()
    patched_sha256 = hashlib.sha256(ready.read_bytes()).hexdigest()
    assert batch["instruction_hash"] == patched["instruction_hash"]
    assert len(attempts) == 1
    assert attempts[0]["instruction_hash"] == patched["instruction_hash"]
    assert attempts[0]["request_file_sha256"] == patched_sha256


def test_formal_tasks_publish_once_and_result_updates_tasks_and_listing(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    repository.insert_tasks(
        [
            _task("e237dc29a715", "CAPPUCCINO-B-60-Z", "46.30", "46.40"),
            _task("15de3a15d0d0", "AISHA-B-60-Z", "26.30", "26.40"),
        ]
    )
    _seed_listing_status(repository)
    manifest = prepare_task_commit_batch(
        repository,
        task_ids=["e237dc29a715", "15de3a15d0d0"],
        mapping_path=Path(MAPPING_PATH),
        batch_id="BATCH-T12-E2E-0001",
        execution_profile="production",
    )
    runner = CapturingRunner()
    request, _ = publish_task_commit_batch(
        repository,
        runner,
        manifest=manifest,
        execution_profile="production",
        applet_uri="weixin://dl/business/?t=test",
    )
    assert len(runner.requests) == 1
    assert "development_confirmation" not in request
    assert manifest["schema_version"] == "shadowbot-commit-manifest-1.2"
    assert request["schema_version"] == "shadowbot-commit-batch-request-1.2"
    assert all(item["operation_id"].startswith("OP-") for item in request["items"])
    assert all(
        item["item_execution_attempt_id"].startswith("ATTEMPT-")
        for item in request["items"]
    )
    assert all(
        item["write_identity_key"].endswith("|" + item["internal_sku"])
        for item in request["items"]
    )
    _v4_validate_request(request)
    assert {repository.get_task(task_id).task_status for task_id in ("e237dc29a715", "15de3a15d0d0")} == {TaskStatus.RUNNING}
    with repository.connect_read() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM shadowbot_write_locks WHERE status = 'ACTIVE'"
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM shadowbot_execution_attempts WHERE status = 'RUNNING'"
            ).fetchone()[0]
            == 2
        )

    result_items = []
    for ordinal, item in enumerate(reversed(request["items"]), start=1):
        result_items.append(
            _complete_result_item({
                **item,
                "preflight_row": ordinal,
                "preflight_price": item["expected_old_price"],
                "execution_ordinal": ordinal,
                "submit_attempted": True,
                "actual_price": item["target_price"],
                "status": "VERIFIED",
                "error_code": "",
                "error_message": "",
            })
        )
    result = {
        "schema_version": "shadowbot-commit-batch-result-1.1",
        "contract_version": 4,
        "result_id": "RESULT-T12-E2E-0001",
        "batch_id": request["batch_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "instruction_hash": request["instruction_hash"],
        "manifest_sha256": request["manifest_sha256"],
        "batch_status": "VERIFIED",
        "items": result_items,
        "counts": {
            "total": 2,
            "attempted": 2,
            "verified": 2,
            "not_applied": 0,
            "failed": 0,
            "unknown": 0,
            "not_attempted": 0,
        },
    }
    assert import_task_commit_result(repository, result)["verified"] == 2
    assert import_task_commit_result(repository, result)["verified"] == 2
    assert {repository.get_task(task_id).task_status for task_id in ("e237dc29a715", "15de3a15d0d0")} == {TaskStatus.SUCCESS}
    with repository.connect_read() as connection:
        prices = {
            row["internal_sku"]: row["current_price"]
            for row in connection.execute("SELECT internal_sku, current_price FROM listing_status")
        }
    assert prices == {"CAPPUCCINO-B-60-Z": "46.40", "AISHA-B-60-Z": "26.40"}


def test_v4_queue_importer_validates_imports_and_archives_verified_batch(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    repository.insert_tasks(
        [
            _task("e237dc29a715", "CAPPUCCINO-B-60-Z", "46.30", "46.40"),
            _task("15de3a15d0d0", "AISHA-B-60-Z", "26.30", "26.40"),
        ]
    )
    _seed_listing_status(repository)
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO listing_status(
                listing_status_id, platform_name, internal_sku, variety, grade,
                current_price, platform_stock_qty, sold_qty, online_status,
                source, updated_at, inventory_source,
                inventory_source_attempt_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'online', 'test', ?, 'default', '')
            """,
            (
                "LISTING-TEST-CAP-C",
                "蚂蚁花团供应商",
                "CAPPUCCINO-C-60-Z",
                "卡布奇诺",
                "C",
                "30.00",
                3,
                "2026-07-22T00:00:00+00:00",
            ),
        )
    queue_dir = tmp_path / "queue"
    request, _, runner = _publish_file_queue_batch(
        repository,
        queue_dir,
        ["e237dc29a715", "15de3a15d0d0"],
        "BATCH-T12-V4-IMPORT-0001",
    )
    result_items = [
        {
            **item,
            "preflight_row": ordinal,
            "preflight_price": item["expected_old_price"],
            "execution_ordinal": ordinal,
            "submit_attempted": True,
            "actual_price": item["target_price"],
            "status": "VERIFIED",
            "error_code": "",
            "error_message": "",
        }
        for ordinal, item in enumerate(request["items"], start=1)
    ]
    page_snapshot = {
        "capture_basis": "BATCH_PREFLIGHT_PLUS_COMMIT_READBACK",
        "captured_at": "2026-07-22T04:59:00+00:00",
        "finalized_at": "2026-07-22T05:00:00+00:00",
        "platform_name": "蚂蚁花团供应商",
        "total_count": 3,
        "products": [
            {
                "position": 1,
                "parent_index": 1,
                "product_name": "卡布奇诺",
                "grade": "B级",
                "price": "46.40",
                "price_status": "VERIFIED_AFTER_COMMIT",
                "inventory": 7,
                "listing_status": "ONLINE",
            },
            {
                "position": 4,
                "parent_index": 49,
                "product_name": "艾莎",
                "grade": "B级",
                "price": "26.40",
                "price_status": "VERIFIED_AFTER_COMMIT",
                "inventory": 4,
                "listing_status": "ONLINE",
            },
            {
                "position": 5,
                "parent_index": 65,
                "product_name": "卡布奇诺",
                "grade": "C级",
                "price": "30.00",
                "price_status": "OBSERVED_AT_PREFLIGHT",
                "inventory": 3,
                "listing_status": "ONLINE",
            },
        ],
    }
    result_path = _write_v4_result(
        queue_dir,
        request,
        result_items,
        "VERIFIED",
        page_snapshot=page_snapshot,
    )

    event = ShadowBotResultImporter(repository, runner, queue_dir).import_one(result_path)

    assert event["status"] == "IMPORTED"
    assert event["contract_version"] == 4
    assert event["counts"]["verified"] == 2
    assert len(event["listing_status_events"]) == 3
    assert {repository.get_task(task_id).task_status for task_id in ("e237dc29a715", "15de3a15d0d0")} == {TaskStatus.SUCCESS}
    archive = Path(event["archive_dir"])
    assert (archive / f"{request['execution_attempt_id']}.request.json").exists()
    assert (archive / f"{request['execution_attempt_id']}.result.json").exists()
    assert (archive / f"{request['execution_attempt_id']}.import.ack.json").exists()
    assert not list((queue_dir / "working").glob("*"))
    assert not list((queue_dir / "results").glob("*"))
    listing_rows = {
        (status.variety, status.grade): status
        for status in repository.list_listing_statuses(platform_name="蚂蚁花团供应商")
    }
    assert listing_rows[("卡布奇诺", "B")].current_price == Decimal("46.40")
    assert listing_rows[("卡布奇诺", "B")].platform_stock_qty == 7
    assert listing_rows[("卡布奇诺", "B")].source == "shadowbot_commit_v4_page_snapshot"
    assert (
        listing_rows[("卡布奇诺", "C")].source
        == "shadowbot_commit_v4_page_snapshot"
    )
    assert listing_rows[("艾莎", "B")].current_price == Decimal("26.40")
    assert listing_rows[("艾莎", "B")].platform_stock_qty == 4
    with repository.connect_read() as connection:
        receipt = connection.execute(
            "SELECT * FROM shadowbot_commit_result_receipts WHERE result_id = ?",
            ("RESULT-" + request["execution_attempt_id"],),
        ).fetchone()
    assert receipt is not None
    assert receipt["ack_state"] == "WRITTEN"
    with repository.connect_read() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM shadowbot_write_locks WHERE status = 'RELEASED'"
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM shadowbot_operations WHERE status = 'VERIFIED'"
            ).fetchone()[0]
            == 2
        )

    archived_result = archive / f"{request['execution_attempt_id']}.result.json"
    replay_result = queue_dir / "results" / archived_result.name
    replay_result.write_bytes(archived_result.read_bytes())
    replay_result.with_suffix(replay_result.suffix + ".sha256").write_text(
        hashlib.sha256(replay_result.read_bytes()).hexdigest() + "\n",
        encoding="ascii",
    )
    replay = ShadowBotResultImporter(repository, runner, queue_dir).import_one(replay_result)
    assert replay["status"] == "ALREADY_IMPORTED"
    assert (archive / f"{request['execution_attempt_id']}.request.json").exists()
    assert (archive / f"{request['execution_attempt_id']}.result.json").exists()


def test_v4_queue_importer_preserves_partial_and_unknown_item_states(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    repository.insert_tasks(
        [
            _task("e237dc29a715", "CAPPUCCINO-B-60-Z", "46.30", "46.40"),
            _task("15de3a15d0d0", "AISHA-B-60-Z", "26.30", "26.40"),
        ]
    )
    _seed_listing_status(repository)
    queue_dir = tmp_path / "queue"
    request, _, runner = _publish_file_queue_batch(
        repository,
        queue_dir,
        ["e237dc29a715", "15de3a15d0d0"],
        "BATCH-T12-V4-PARTIAL-0001",
    )
    first, second = request["items"]
    result_items = [
        _complete_result_item({
            **first,
            "preflight_row": 1,
            "preflight_price": first["expected_old_price"],
            "execution_ordinal": 1,
            "submit_attempted": True,
            "actual_price": first["target_price"],
            "status": "VERIFIED",
            "error_code": "",
            "error_message": "",
        }),
        _complete_result_item({
            **second,
            "preflight_row": 4,
            "preflight_price": second["expected_old_price"],
            "execution_ordinal": 2,
            "submit_attempted": True,
            "actual_price": None,
            "status": "UNKNOWN",
            "error_code": "SUBMIT_RESULT_UNKNOWN",
            "error_message": "提交后无法唯一回读",
        }),
    ]
    result_path = _write_v4_result(queue_dir, request, result_items, "PARTIAL")

    event = ShadowBotResultImporter(repository, runner, queue_dir).import_one(result_path)

    assert event["counts"]["verified"] == 1
    assert event["counts"]["unknown"] == 1
    assert repository.get_task(first["source_task_id"]).task_status is TaskStatus.SUCCESS
    assert repository.get_task(second["source_task_id"]).task_status is TaskStatus.MANUAL_REVIEW


def test_v4_watchdog_accepts_queued_ready_request(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    repository.insert_tasks(
        [
            _task("e237dc29a715", "CAPPUCCINO-B-60-Z", "46.30", "46.40"),
            _task("15de3a15d0d0", "AISHA-B-60-Z", "26.30", "26.40"),
        ]
    )
    _seed_listing_status(repository)
    queue_dir = tmp_path / "queue"
    request, working, _ = _publish_file_queue_batch(
        repository,
        queue_dir,
        ["e237dc29a715", "15de3a15d0d0"],
        "BATCH-T12-V4-WATCHDOG-0001",
    )
    working_checksum = working.with_suffix(working.suffix + ".sha256")
    ready = queue_dir / "inbox" / f"{request['execution_attempt_id']}.ready.json"
    ready_checksum = ready.with_suffix(ready.suffix + ".sha256")
    os.replace(working, ready)
    os.replace(working_checksum, ready_checksum)

    events = ShadowBotQueueWatchdog(
        queue_dir,
        stale_seconds=30,
        repository=repository,
    ).inspect()

    assert events == []
    assert ready.exists()


def test_v4_watchdog_recovers_partial_unknown_and_importer_preserves_boundary(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    repository.insert_tasks(
        [
            _task("e237dc29a715", "CAPPUCCINO-B-60-Z", "46.30", "46.40"),
            _task("15de3a15d0d0", "AISHA-B-60-Z", "26.30", "26.40"),
        ]
    )
    _seed_listing_status(repository)
    queue_dir = tmp_path / "queue"
    request, working, runner = _publish_file_queue_batch(
        repository,
        queue_dir,
        ["e237dc29a715", "15de3a15d0d0"],
        "BATCH-T12-V4-RECOVERY-0001",
    )
    first, second = request["items"]
    snapshot_items = [
        _complete_result_item({
            **first,
            "preflight_row": 1,
            "preflight_price": first["expected_old_price"],
            "execution_ordinal": 1,
            "submit_attempted": True,
            "actual_price": first["target_price"],
            "status": "VERIFIED",
            "error_code": "",
            "error_message": "",
        }),
        _complete_result_item({
            **second,
            "preflight_row": 4,
            "preflight_price": second["expected_old_price"],
            "execution_ordinal": 2,
            "submit_attempted": False,
            "actual_price": None,
            "status": "NOT_ATTEMPTED",
            "error_code": "",
            "error_message": "",
        }),
    ]
    old_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    phase = {
        "task_id": request["task_id"],
        "operation_id": request["operation_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "execution_mode": "COMMIT",
        "phase": "SUBMIT_CLICKED",
        "side_effect_state": "SUBMIT_CLICKED",
        "request_file_sha256": hashlib.sha256(working.read_bytes()).hexdigest(),
        "instruction_hash": request["instruction_hash"],
        "worker_id": "TEST-V4-WORKER",
        "current_source_task_id": second["source_task_id"],
        "execution_ordinal": 2,
        "batch_result_snapshot": {
            "contract_version": 4,
            "items": snapshot_items,
        },
        "updated_at": old_time.isoformat(),
    }
    phase_path = queue_dir / "working" / f"{request['execution_attempt_id']}.phase.json"
    phase_path.write_text(json.dumps(phase, ensure_ascii=False), encoding="utf-8")
    (queue_dir / "heartbeat.json").write_text(
        json.dumps(
            {
                "worker_id": "TEST-V4-WORKER",
                "status": "RUNNING",
                "updated_at": old_time.isoformat(),
            }
        ),
        encoding="utf-8",
    )

    events = ShadowBotQueueWatchdog(
        queue_dir,
        stale_seconds=30,
        repository=repository,
    ).inspect()

    assert events[0]["status"] == "RECOVERY_RESULT_WRITTEN"
    result_path = Path(events[0]["result_path"])
    recovered = json.loads(result_path.read_text(encoding="utf-8"))
    assert recovered["batch_status"] == "PARTIAL"
    assert recovered["counts"] == {
        "total": 2,
        "attempted": 2,
        "verified": 1,
        "not_applied": 0,
        "failed": 0,
        "unknown": 1,
        "not_attempted": 0,
    }
    assert [item["status"] for item in recovered["items"]] == ["VERIFIED", "UNKNOWN"]

    imported = ShadowBotResultImporter(repository, runner, queue_dir).import_one(result_path)
    assert imported["status"] == "IMPORTED"
    assert repository.get_task(first["source_task_id"]).task_status is TaskStatus.SUCCESS
    assert repository.get_task(second["source_task_id"]).task_status is TaskStatus.MANUAL_REVIEW


def test_manifest_preview_does_not_persist_batch_or_claim_task(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    repository.insert_task(
        _task("e237dc29a715", "CAPPUCCINO-B-60-Z", "46.30", "46.40")
    )
    _seed_listing_status(repository)

    manifest = build_task_commit_manifest(
        repository,
        task_ids=["e237dc29a715"],
        mapping_path=Path(MAPPING_PATH),
        batch_id="BATCH-T12-PLAN-ONLY-0001",
    )

    assert manifest["items"][0]["source_task_id"] == "e237dc29a715"
    assert repository.get_task("e237dc29a715").task_status is TaskStatus.PENDING
    with repository.connect_read() as connection:
        batch_count = connection.execute(
            "SELECT COUNT(*) FROM shadowbot_commit_batches WHERE batch_id = ?",
            ("BATCH-T12-PLAN-ONLY-0001",),
        ).fetchone()[0]
    assert batch_count == 0


def test_not_attempted_task_returns_to_pending(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    repository.insert_task(_task("e237dc29a715", "CAPPUCCINO-B-60-Z", "46.30", "46.40"))
    _seed_listing_status(repository)
    manifest = prepare_task_commit_batch(
        repository,
        task_ids=["e237dc29a715"],
        mapping_path=Path(MAPPING_PATH),
        batch_id="BATCH-T12-E2E-0002",
        execution_profile="production",
    )
    runner = CapturingRunner()
    request, _ = publish_task_commit_batch(
        repository,
        runner,
        manifest=manifest,
        execution_profile="production",
        applet_uri="weixin://dl/business/?t=test",
    )
    item = _complete_result_item({
        **request["items"][0],
        "preflight_row": 1,
        "preflight_price": "47.00",
        "execution_ordinal": None,
        "submit_attempted": False,
        "actual_price": None,
        "status": "NOT_ATTEMPTED",
        "error_code": "OLD_PRICE_CHANGED",
        "error_message": "页面旧价不一致",
    })
    import_task_commit_result(
        repository,
        {
            "schema_version": "shadowbot-commit-batch-result-1.1",
            "contract_version": 4,
            "result_id": "RESULT-T12-E2E-0002",
            "batch_id": request["batch_id"],
            "execution_attempt_id": request["execution_attempt_id"],
            "instruction_hash": request["instruction_hash"],
            "manifest_sha256": request["manifest_sha256"],
            "batch_status": "FAILED",
            "items": [item],
            "counts": {"total": 1, "attempted": 0, "verified": 0, "not_applied": 0, "failed": 0, "unknown": 0, "not_attempted": 1},
        },
    )
    assert repository.get_task("e237dc29a715").task_status is TaskStatus.PENDING


def test_pre_submit_failed_item_returns_to_pending_when_batch_never_started(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    repository.insert_task(_task("e237dc29a715", "CAPPUCCINO-B-60-Z", "46.30", "46.40"))
    _seed_listing_status(repository)
    manifest = prepare_task_commit_batch(
        repository,
        task_ids=["e237dc29a715"],
        mapping_path=Path(MAPPING_PATH),
        batch_id="BATCH-T12-PRESUBMIT-FAILED-0001",
        execution_profile="production",
    )
    request, _ = publish_task_commit_batch(
        repository,
        CapturingRunner(),
        manifest=manifest,
        execution_profile="production",
        applet_uri="weixin://dl/business/?t=test",
    )
    item = _complete_result_item({
        **request["items"][0],
        "preflight_row": 1,
        "preflight_price": "46.30",
        "execution_ordinal": 1,
        "submit_attempted": False,
        "actual_price": None,
        "status": "FAILED",
        "error_code": "PRODUCT_POSITION_HINT_MISMATCH",
        "error_message": "等级后缀不一致",
    })

    import_task_commit_result(
        repository,
        {
            "schema_version": "shadowbot-commit-batch-result-1.1",
            "contract_version": 4,
            "result_id": "RESULT-T12-PRESUBMIT-FAILED-0001",
            "batch_id": request["batch_id"],
            "execution_attempt_id": request["execution_attempt_id"],
            "instruction_hash": request["instruction_hash"],
            "manifest_sha256": request["manifest_sha256"],
            "batch_status": "FAILED",
            "side_effect_state": "NOT_STARTED",
            "items": [item],
            "counts": {
                "total": 1,
                "attempted": 0,
                "verified": 0,
                "not_applied": 0,
                "failed": 1,
                "unknown": 0,
                "not_attempted": 0,
            },
        },
    )

    task = repository.get_task("e237dc29a715")
    assert task.task_status is TaskStatus.PENDING
    assert "提交前失败，任务保留 pending" in task.result_message


def test_naive_task_deadline_uses_business_timezone(monkeypatch):
    monkeypatch.setenv("PRA_BUSINESS_TIMEZONE", "Asia/Shanghai")
    converted = _task_time_as_utc(datetime(2026, 7, 22, 1, 46, 0))
    assert converted == datetime(2026, 7, 21, 17, 46, 0, tzinfo=timezone.utc)


def test_publish_rechecks_task_expiry_after_prepare(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    repository.insert_task(_task("e237dc29a715", "CAPPUCCINO-B-60-Z", "46.30", "46.40"))
    _seed_listing_status(repository)
    manifest = prepare_task_commit_batch(
        repository,
        task_ids=["e237dc29a715"],
        mapping_path=Path(MAPPING_PATH),
        batch_id="BATCH-T12-EXPIRES-0001",
        execution_profile="production",
    )
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            "UPDATE tasks SET expires_at = ? WHERE task_id = ?",
            ("2026-07-22T01:46:00", "e237dc29a715"),
        )
    runner = CapturingRunner()
    with pytest.raises(ValidationError, match="任务在发布前已变化"):
        publish_task_commit_batch(
            repository,
            runner,
            manifest=manifest,
            execution_profile="production",
            applet_uri="weixin://dl/business/?t=test",
        )
    assert runner.requests == []
    assert repository.get_task("e237dc29a715").task_status is TaskStatus.PENDING


@pytest.mark.parametrize(
    ("published", "batch_status", "task_status", "attempt_status", "lock_status"),
    [
        (False, "PREPARED", TaskStatus.PENDING, "START_FAILED", "RELEASED"),
        (True, "UNKNOWN", TaskStatus.MANUAL_REVIEW, "START_UNKNOWN", "UNKNOWN"),
    ],
)
def test_publish_boundary_compensates_or_fences_every_item(
    tmp_path,
    published,
    batch_status,
    task_status,
    attempt_status,
    lock_status,
):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    repository.insert_task(
        _task("e237dc29a715", "CAPPUCCINO-B-60-Z", "46.30", "46.40")
    )
    _seed_listing_status(repository)
    manifest = prepare_task_commit_batch(
        repository,
        task_ids=["e237dc29a715"],
        mapping_path=Path(MAPPING_PATH),
        batch_id=f"BATCH-T12-BOUNDARY-{int(published)}",
        execution_profile="production",
    )

    with pytest.raises(ShadowBotStartBoundaryError):
        publish_task_commit_batch(
            repository,
            BoundaryRunner(published=published),
            manifest=manifest,
            execution_profile="production",
            applet_uri="weixin://dl/business/?t=test",
        )

    assert repository.get_task("e237dc29a715").task_status is task_status
    with repository.connect_read() as connection:
        batch = connection.execute(
            "SELECT status FROM shadowbot_commit_batches WHERE batch_id = ?",
            (manifest["batch_id"],),
        ).fetchone()
        item = connection.execute(
            "SELECT * FROM shadowbot_commit_batch_items WHERE batch_id = ?",
            (manifest["batch_id"],),
        ).fetchone()
        attempt = connection.execute(
            "SELECT status FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?",
            (item["item_execution_attempt_id"],),
        ).fetchone()
        write_lock = connection.execute(
            "SELECT status FROM shadowbot_write_locks WHERE write_identity_key = ?",
            (item["write_identity_key"],),
        ).fetchone()
    assert batch["status"] == batch_status
    assert attempt["status"] == attempt_status
    assert write_lock["status"] == lock_status


def test_import_rejects_truthy_string_bool_without_partial_write(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    repository.insert_task(
        _task("e237dc29a715", "CAPPUCCINO-B-60-Z", "46.30", "46.40")
    )
    _seed_listing_status(repository)
    manifest = prepare_task_commit_batch(
        repository,
        task_ids=["e237dc29a715"],
        mapping_path=Path(MAPPING_PATH),
        batch_id="BATCH-T12-STRICT-BOOL-0001",
        execution_profile="production",
    )
    request, _ = publish_task_commit_batch(
        repository,
        CapturingRunner(),
        manifest=manifest,
        execution_profile="production",
        applet_uri="weixin://dl/business/?t=test",
    )
    item = _complete_result_item(
        {
            **request["items"][0],
            "preflight_row": 1,
            "preflight_price": "46.30",
            "execution_ordinal": 1,
            "submit_attempted": "false",
            "actual_price": None,
            "status": "FAILED",
            "error_code": "TEST",
            "error_message": "invalid boolean",
        }
    )
    result = {
        "schema_version": "shadowbot-commit-batch-result-1.1",
        "contract_version": 4,
        "result_id": "RESULT-T12-STRICT-BOOL-0001",
        "batch_id": request["batch_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "instruction_hash": request["instruction_hash"],
        "manifest_sha256": request["manifest_sha256"],
        "batch_status": "FAILED",
        "items": [item],
        "counts": {
            "total": 1,
            "attempted": 0,
            "verified": 0,
            "not_applied": 0,
            "failed": 1,
            "unknown": 0,
            "not_attempted": 0,
        },
    }

    with pytest.raises(ValidationError, match="submit_attempted"):
        import_task_commit_result(repository, result)

    assert repository.get_task("e237dc29a715").task_status is TaskStatus.RUNNING
    with repository.connect_read() as connection:
        receipt_count = connection.execute(
            "SELECT COUNT(*) FROM shadowbot_commit_result_receipts"
        ).fetchone()[0]
    assert receipt_count == 0

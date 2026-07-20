from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import copy
import sys
import types
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import pytest

from app.enums import ReviewTaskStatus, TaskActionType, TaskStatus
from app.models import ReviewTask, Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_executor import (
    SIDE_EFFECT_NOT_STARTED,
    SIDE_EFFECT_UNKNOWN,
    STATUS_READ_COMPLETED,
    STATUS_SIDE_EFFECT_UNKNOWN,
    ShadowBotFileQueueRunner,
    ShadowBotResultContract,
    compute_instruction_hash,
)
from app.services.shadowbot_price_batch import (
    BatchItemStatus,
    compute_batch_item_approved_payload_hash,
    normalize_price_batch_request,
)
from app.services.shadowbot_price_batch_execution import ShadowBotPriceBatchExecutionService
from app.services.shadowbot_price_batch_orchestrator import ShadowBotPriceBatchOrchestrator
from app.services.shadowbot_price_batch_report import (
    build_price_batch_acceptance,
    render_price_batch_markdown,
    validate_price_batch_acceptance,
    write_price_batch_reports,
)
from app.services.shadowbot_queue import ShadowBotResultImporter
from app import web


PLATFORM_KEY = "ant_flower_wechat"
PLATFORM_NAME = "蚂蚁花团供应商"


def _prepare(tmp_path, *, mode="COMMIT"):
    now = datetime.now(UTC).replace(microsecond=0)
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    source = {
        "source_read_batch_id": "READ-BATCH-PHASE4-001",
        "source_snapshot_sha256": "sha256:" + "1" * 64,
        "source_page_context_sha256": "sha256:" + "2" * 64,
        "source_observed_at": now.isoformat(),
        "source_snapshot_max_age_seconds": 300,
        "platform": PLATFORM_KEY,
        "page_context": {"platform": PLATFORM_KEY, "platform_name": PLATFORM_NAME},
        "source_items": {
            "READ-ITEM-001": {
                "item_id": "READ-ITEM-001",
                "product_name": "艾莎",
                "grade": "B级",
                "price": "8.60",
                "listing_status": "ONLINE",
                "observed_at": now.isoformat(),
            }
        },
    }
    request = {
        "contract_version": 3,
        "batch_id": "PRICE-BATCH-PHASE4-001",
        "platform": PLATFORM_KEY,
        "batch_type": "SERIAL_PRICE_UPDATE",
        "execution_mode": mode,
        "stop_policy": "PAUSE_ON_UNCERTAIN",
        "capture_evidence": False,
        "source_read_batch_id": source["source_read_batch_id"],
        "source_snapshot_sha256": source["source_snapshot_sha256"],
        "source_page_context_sha256": source["source_page_context_sha256"],
        "source_observed_at": now.isoformat(),
        "source_snapshot_max_age_seconds": 300,
        "items": [
            {
                "item_id": "ITEM-001",
                "ordinal": 1,
                "source_item_id": "READ-ITEM-001",
                "task_id": "TASK-001",
                "review_task_id": "REVIEW-001",
                "operation_id": "OP-001",
                "approved_payload_hash": "sha256:" + "0" * 64,
                "platform_sku": "SKU-AISHA-B",
                "expected_product_name": "艾莎",
                "expected_grade": "B级",
                "approved_expected_old_price": "8.60",
                "target_price": "8.80",
            }
        ],
    }
    initial = normalize_price_batch_request(request, source_binding=source, now=now)
    request["items"][0]["approved_payload_hash"] = compute_batch_item_approved_payload_hash(
        initial,
        initial["items"][0],
    )
    normalized = normalize_price_batch_request(request, source_binding=source, now=now)
    item = normalized["items"][0]
    repository.insert_task(
        Task(
            task_id=item["task_id"],
            internal_sku=item["platform_sku"],
            platform_name=PLATFORM_NAME,
            action_type=TaskActionType.UPDATE_PRICE,
            priority=100,
            task_status=TaskStatus.PENDING,
            created_at=now,
            target_price=Decimal(item["target_price"]),
            scope_type="sku",
            scope_key=item["platform_sku"],
            dedupe_key="TASK-001|update_price",
        )
    )
    repository.insert_review_tasks(
        [
            ReviewTask(
                review_task_id=item["review_task_id"],
                trade_date=None,
                scope_type="sku",
                scope_key=item["platform_sku"],
                dedupe_key=item["review_task_id"],
                source_task_id=item["task_id"],
                review_type="price_update",
                review_status=ReviewTaskStatus.APPROVED,
                internal_sku=item["platform_sku"],
                platform_name=PLATFORM_NAME,
                resolution_payload={
                    "approved_payload_hash": item["approved_payload_hash"],
                    "approved_execution_modes": [mode],
                    "approval_expires_at": (now + timedelta(hours=1)).isoformat(),
                },
                created_at=now,
                updated_at=now,
                resolved_by="owner",
                resolved_at=now,
            )
        ]
    )
    orchestrator = ShadowBotPriceBatchOrchestrator(repository)
    batch = orchestrator.create_batch(request, source_binding=source, created_by="owner", now=now)
    claimed = orchestrator.claim_next(batch.batch_id, now=now)
    runner = ShadowBotFileQueueRunner(tmp_path / "queue")
    execution = ShadowBotPriceBatchExecutionService(repository, runner)
    return now, repository, orchestrator, execution, batch, claimed, tmp_path / "queue"


def _record_fresh_result(now, repository, execution, orchestrator, batch, item):
    started = execution.start_fresh_read(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="FRESH-ATTEMPT-001",
        now=now,
    )
    attempt = repository.get_shadowbot_execution_attempt(started.execution_attempt_id)
    lease = attempt.raw_output["lease"]
    request_path = Path(attempt.queue_request_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    execution.executor.record_result(
        ShadowBotResultContract(
            execution_attempt_id=attempt.execution_attempt_id,
            status=STATUS_READ_COMPLETED,
            run_success_flag=True,
            business_operation_completed=False,
            side_effect_state=SIDE_EFFECT_NOT_STARTED,
            retryable=False,
            operation_id=item.operation_id,
            task_id=item.task_id,
            execution_mode="READ_ONLY",
            instruction_hash=attempt.instruction_hash,
            request_file_sha256=attempt.request_file_sha256,
            result_id="RESULT-FRESH-001",
            lease_owner_token=lease["owner_token"],
            lease_version=lease["version"],
            raw_output={
                **request,
                "actual_price": "8.60",
                "observed_at": now.isoformat(),
                "result_file_sha256": "a" * 64,
            },
        )
    )
    orchestrator.record_fresh_read(
        batch.batch_id,
        item.item_id,
        fresh_read_attempt_id=attempt.execution_attempt_id,
        result_sha256="sha256:" + "a" * 64,
        observed_product_name="艾莎",
        observed_grade="B级",
        observed_platform_sku="SKU-AISHA-B",
        observed_price="8.60",
        observed_at=now,
        now=now,
    )
    return request


def _load_worker_module():
    path = Path("shadowbot/test2/shadowbot_queue_worker.py")
    spec = importlib.util.spec_from_file_location("shadowbot_queue_worker_task12", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _publish_result(queue_dir, repository, attempt_id, **updates):
    attempt = repository.get_shadowbot_execution_attempt(attempt_id)
    request = json.loads(Path(attempt.queue_request_path).read_text(encoding="utf-8"))
    result = {
        **request,
        "schema_version": "shadowbot-result-1.0",
        "status": "FAILED",
        "run_success_flag": False,
        "business_operation_completed": False,
        "side_effect_state": "NOT_STARTED",
        "retryable": False,
        "error_code": "",
        "error_message": "",
        "result_id": "RESULT-" + attempt_id,
        "request_file_sha256": attempt.request_file_sha256,
        "product_name": request.get("expected_product_name", ""),
        "grade": request.get("expected_grade", ""),
        **updates,
    }
    result_path = queue_dir / "results" / f"{attempt_id}.result.json"
    content = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_bytes(content)
    result_path.with_suffix(result_path.suffix + ".sha256").write_text(
        hashlib.sha256(content).hexdigest() + "\n",
        encoding="ascii",
    )
    return result_path


def _import_verified_batch(now, repository, execution, batch, item, queue_dir):
    importer = ShadowBotResultImporter(repository, ShadowBotFileQueueRunner(queue_dir), queue_dir)
    execution.start_fresh_read(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="FRESH-REPORT-001",
        now=now,
    )
    importer.import_one(
        _publish_result(
            queue_dir,
            repository,
            "FRESH-REPORT-001",
            status="READ_COMPLETED",
            run_success_flag=True,
            business_operation_completed=False,
            product_name="艾莎",
            grade="B级",
            platform_sku="SKU-AISHA-B",
            actual_price="8.60",
            observed_at=now.isoformat(),
        )
    )
    execution.start_write(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="WRITE-REPORT-001",
        now=datetime.now(UTC),
    )
    importer.import_one(
        _publish_result(
            queue_dir,
            repository,
            "WRITE-REPORT-001",
            status="VERIFIED",
            run_success_flag=True,
            business_operation_completed=True,
            side_effect_state="VERIFIED",
            actual_price="8.80",
            post_read_at=datetime.now(UTC).isoformat(),
        )
    )


def test_fresh_read_and_write_are_lowered_to_separate_bound_queue_attempts(tmp_path):
    now, repository, orchestrator, execution, batch, item, queue_dir = _prepare(tmp_path)
    fresh_request = _record_fresh_result(now, repository, execution, orchestrator, batch, item)
    assert fresh_request["batch_contract_version"] == 3
    assert fresh_request["price_batch_stage"] == "FRESH_READ"
    assert fresh_request["execution_mode"] == "READ_ONLY"
    assert fresh_request["capture_evidence"] is False
    assert fresh_request["instruction_hash"] == compute_instruction_hash(fresh_request)
    assert repository.get_shadowbot_operation(item.operation_id).status == "PENDING"

    started = execution.start_write(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="WRITE-ATTEMPT-001",
        now=now,
    )
    write_path = queue_dir / "inbox" / "WRITE-ATTEMPT-001.ready.json"
    write_request = json.loads(write_path.read_text(encoding="utf-8"))
    assert started.status == "RUNNING"
    assert write_request["price_batch_stage"] == "WRITE"
    assert write_request["execution_mode"] == "COMMIT"
    assert write_request["fresh_read_attempt_id"] == "FRESH-ATTEMPT-001"
    assert write_request["fresh_read_result_sha256"] == "sha256:" + "a" * 64
    assert write_request["fresh_old_price"] == "8.60"
    assert write_request["instruction_hash"] == compute_instruction_hash(write_request)
    assert repository.get_shadowbot_batch_item(batch.batch_id, item.item_id).current_execution_attempt_id == "WRITE-ATTEMPT-001"


def test_worker_validates_and_propagates_hashed_batch_binding(tmp_path, monkeypatch):
    now, _, orchestrator, execution, batch, item, queue_dir = _prepare(tmp_path)
    _record_fresh_result(now, execution.repository, execution, orchestrator, batch, item)
    execution.start_write(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="WRITE-ATTEMPT-001",
        now=now,
    )
    request = json.loads((queue_dir / "inbox" / "WRITE-ATTEMPT-001.ready.json").read_text(encoding="utf-8"))
    worker_module = _load_worker_module()
    worker = worker_module.QueueWorker(
        {
            "queue_dir": str(queue_dir),
            "poll_seconds": 1,
            "max_hours": 1,
            "max_tasks": 1,
            "heartbeat_seconds": 5,
        }
    )
    worker._validate_request(request)
    tampered = dict(request)
    tampered["price_batch_item_id"] = "ITEM-TAMPERED"
    with pytest.raises(ValueError, match="instruction_hash mismatch"):
        worker._validate_request(tampered)

    tampered = dict(request)
    tampered["capture_evidence"] = True
    with pytest.raises(ValueError, match="instruction_hash mismatch"):
        worker._validate_request(tampered)

    monkeypatch.setitem(
        sys.modules,
        "vertical_slice_read_price",
        types.SimpleNamespace(
            main=lambda _args: json.dumps(
                {
                    "status": "VERIFIED",
                    "run_success_flag": True,
                    "business_operation_completed": True,
                    "side_effect_state": "VERIFIED",
                    "retryable": False,
                },
                ensure_ascii=False,
            )
        ),
    )
    phase_path = queue_dir / "working" / "WRITE-ATTEMPT-001.phase.json"
    worker._execute_claimed(request, "c" * 64, queue_dir / "working" / "request.json", phase_path)
    phase = json.loads(phase_path.read_text(encoding="utf-8"))
    result = json.loads(
        (queue_dir / "results" / "WRITE-ATTEMPT-001.result.json").read_text(encoding="utf-8")
    )
    for name in worker_module.TASK12_INSTRUCTION_HASH_FIELDS:
        assert phase[name] == request[name]
        assert result[name] == request[name]


def test_task12_unknown_does_not_create_legacy_automatic_reconcile(tmp_path):
    now, repository, orchestrator, execution, batch, item, queue_dir = _prepare(tmp_path)
    _record_fresh_result(now, repository, execution, orchestrator, batch, item)
    started = execution.start_write(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="WRITE-UNKNOWN-001",
        now=now,
    )
    attempt = repository.get_shadowbot_execution_attempt(started.execution_attempt_id)
    lease = attempt.raw_output["lease"]
    request = json.loads((queue_dir / "inbox" / "WRITE-UNKNOWN-001.ready.json").read_text(encoding="utf-8"))
    execution.executor.record_result(
        ShadowBotResultContract(
            execution_attempt_id=attempt.execution_attempt_id,
            status=STATUS_SIDE_EFFECT_UNKNOWN,
            run_success_flag=None,
            business_operation_completed=None,
            side_effect_state=SIDE_EFFECT_UNKNOWN,
            retryable=False,
            error_code="SUBMIT_RESULT_UNKNOWN",
            operation_id=item.operation_id,
            task_id=item.task_id,
            execution_mode="COMMIT",
            instruction_hash=attempt.instruction_hash,
            request_file_sha256=attempt.request_file_sha256,
            result_id="RESULT-UNKNOWN-001",
            lease_owner_token=lease["owner_token"],
            lease_version=lease["version"],
            raw_output=request,
        ),
        automatic_reconcile_payload={"applet_uri": "should-not-be-used"},
    )
    attempts = repository.list_shadowbot_execution_attempts(operation_id=item.operation_id)
    assert [attempt.execution_attempt_id for attempt in attempts] == [
        "FRESH-ATTEMPT-001",
        "WRITE-UNKNOWN-001",
    ]
    assert repository.get_shadowbot_operation(item.operation_id).status == "NEEDS_RECONCILIATION"

    orchestrator.record_item_result(
        batch.batch_id,
        item.item_id,
        status=BatchItemStatus.NEEDS_RECONCILIATION.value,
        execution_attempt_id="WRITE-UNKNOWN-001",
        error_code="SUBMIT_RESULT_UNKNOWN",
        result_id="RESULT-UNKNOWN-001",
        result_hash="sha256:" + "b" * 64,
        now=now,
    )
    orchestrator.claim_reconcile(
        batch.batch_id,
        item.item_id,
        reconcile_attempt_id="RECONCILE-TASK12-001",
        now=now,
    )
    execution.start_reconcile(
        batch.batch_id,
        item.item_id,
        reconcile_attempt_id="RECONCILE-TASK12-001",
    )
    reconcile_request = json.loads(
        (queue_dir / "inbox" / "RECONCILE-TASK12-001.ready.json").read_text(encoding="utf-8")
    )
    assert reconcile_request["price_batch_stage"] == "RECONCILE"
    assert reconcile_request["execution_mode"] == "RECONCILE"
    assert reconcile_request["price_batch_item_id"] == item.item_id


def test_vertical_phase_writer_carries_every_hashed_task12_field():
    worker_module = _load_worker_module()
    source = Path("shadowbot/test2/vertical_slice_read_price.py").read_text(encoding="utf-8")
    start = source.index("def _write_phase(")
    end = source.index("def _check_stop_before_submit(", start)
    phase_writer = source[start:end]

    for name in worker_module.TASK12_INSTRUCTION_HASH_FIELDS:
        assert f'"{name}"' in phase_writer


def test_result_importer_projects_fresh_read_and_verified_write_to_batch(tmp_path):
    now, repository, orchestrator, execution, batch, item, queue_dir = _prepare(tmp_path)
    execution.start_fresh_read(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="FRESH-IMPORT-001",
        now=now,
    )
    fresh_path = _publish_result(
        queue_dir,
        repository,
        "FRESH-IMPORT-001",
        status="READ_COMPLETED",
        run_success_flag=True,
        business_operation_completed=False,
        product_name="艾莎",
        grade="B级",
        platform_sku="SKU-AISHA-B",
        actual_price="8.60",
        observed_at=now.isoformat(),
    )
    importer = ShadowBotResultImporter(repository, ShadowBotFileQueueRunner(queue_dir), queue_dir)
    fresh_event = importer.import_one(fresh_path)
    stored = repository.get_shadowbot_batch_item(batch.batch_id, item.item_id)
    assert fresh_event["price_batch"]["stage"] == "FRESH_READ"
    assert stored.fresh_read_attempt_id == "FRESH-IMPORT-001"
    assert stored.fresh_old_price == Decimal("8.60")

    execution.start_write(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="WRITE-IMPORT-001",
        now=datetime.now(UTC),
    )
    write_path = _publish_result(
        queue_dir,
        repository,
        "WRITE-IMPORT-001",
        status="VERIFIED",
        run_success_flag=True,
        business_operation_completed=True,
        side_effect_state="VERIFIED",
        actual_price="8.80",
        post_read_at=now.isoformat(),
    )
    write_event = importer.import_one(write_path)
    stored = repository.get_shadowbot_batch_item(batch.batch_id, item.item_id)
    stored_batch = repository.get_shadowbot_batch(batch.batch_id)
    assert write_event["price_batch"]["counts"]["verified_count"] == 1
    assert stored.status == "VERIFIED"
    assert stored.post_commit_price == Decimal("8.80")
    assert stored.current_run_id == "filequeue:WRITE-IMPORT-001"
    assert stored_batch.status == "COMPLETED"


def test_result_importer_quarantines_task12_binding_tamper(tmp_path):
    now, repository, _, execution, batch, item, queue_dir = _prepare(tmp_path)
    execution.start_fresh_read(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="FRESH-TAMPER-001",
        now=now,
    )
    _publish_result(
        queue_dir,
        repository,
        "FRESH-TAMPER-001",
        status="READ_COMPLETED",
        run_success_flag=True,
        business_operation_completed=False,
        product_name="艾莎",
        grade="B级",
        platform_sku="SKU-AISHA-B",
        actual_price="8.60",
        observed_at=now.isoformat(),
        price_batch_item_id="ITEM-TAMPERED",
    )
    importer = ShadowBotResultImporter(repository, ShadowBotFileQueueRunner(queue_dir), queue_dir)
    events = importer.import_available()
    assert events[0]["status"] == "QUARANTINED"
    assert events[0]["error_code"] == "RESULT_CONTRACT_INVALID"
    assert repository.get_shadowbot_execution_attempt("FRESH-TAMPER-001").ended_at is not None
    assert repository.get_shadowbot_batch_item(batch.batch_id, item.item_id).status == "RUNNING"


def test_result_importer_routes_unknown_through_unique_batch_reconcile(tmp_path):
    now, repository, orchestrator, execution, batch, item, queue_dir = _prepare(tmp_path)
    execution.start_fresh_read(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="FRESH-UNKNOWN-001",
        now=now,
    )
    importer = ShadowBotResultImporter(repository, ShadowBotFileQueueRunner(queue_dir), queue_dir)
    importer.import_one(
        _publish_result(
            queue_dir,
            repository,
            "FRESH-UNKNOWN-001",
            status="READ_COMPLETED",
            run_success_flag=True,
            business_operation_completed=False,
            product_name="艾莎",
            grade="B级",
            platform_sku="SKU-AISHA-B",
            actual_price="8.60",
            observed_at=now.isoformat(),
        )
    )
    execution.start_write(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="WRITE-UNKNOWN-IMPORT-001",
        now=datetime.now(UTC),
    )
    unknown_event = importer.import_one(
        _publish_result(
            queue_dir,
            repository,
            "WRITE-UNKNOWN-IMPORT-001",
            status="SIDE_EFFECT_UNKNOWN",
            run_success_flag=None,
            business_operation_completed=None,
            side_effect_state="UNKNOWN",
            error_code="FINAL_SAVE_NOT_FOUND",
            error_message="final save unavailable",
            actual_price="8.60",
        )
    )
    stored = repository.get_shadowbot_batch_item(batch.batch_id, item.item_id)
    assert unknown_event["price_batch"]["item_status"] == "NEEDS_RECONCILIATION"
    assert stored.error_code == "SUBMIT_RESULT_UNKNOWN"
    assert "adapter_error_code=FINAL_SAVE_NOT_FOUND" in stored.error_message
    assert len(repository.list_shadowbot_execution_attempts(operation_id=item.operation_id)) == 2

    orchestrator.claim_reconcile(
        batch.batch_id,
        item.item_id,
        reconcile_attempt_id="RECONCILE-IMPORT-001",
        now=datetime.now(UTC),
    )
    execution.start_reconcile(
        batch.batch_id,
        item.item_id,
        reconcile_attempt_id="RECONCILE-IMPORT-001",
    )
    reconcile_event = importer.import_one(
        _publish_result(
            queue_dir,
            repository,
            "RECONCILE-IMPORT-001",
            status="VERIFIED",
            run_success_flag=True,
            business_operation_completed=True,
            side_effect_state="VERIFIED",
            actual_price="8.80",
            observed_at=now.isoformat(),
        )
    )
    stored = repository.get_shadowbot_batch_item(batch.batch_id, item.item_id)
    assert reconcile_event["price_batch"]["item_status"] == "VERIFIED"
    assert stored.reconciliation_outcome == "VERIFIED"
    assert len(repository.list_shadowbot_execution_attempts(operation_id=item.operation_id)) == 3


def test_price_batch_acceptance_and_markdown_are_recomputed_from_item_records(tmp_path):
    now, repository, _, execution, batch, item, queue_dir = _prepare(tmp_path)
    _import_verified_batch(now, repository, execution, batch, item, queue_dir)
    payload = build_price_batch_acceptance(repository, batch.batch_id, queue_dir=queue_dir)
    assert payload["overall_status"] == "PASSED"
    assert payload["count_identity"]["passed"] is True
    assert payload["count_identity"]["recomputed"]["verified_count"] == 1
    assert payload["validation"]["no_cross_product_attempt_binding"] is True
    assert all(attempt["archive"]["passed"] for attempt in payload["items"][0]["attempts"])
    assert validate_price_batch_acceptance(payload)["passed"] is True

    tampered = copy.deepcopy(payload)
    tampered["count_identity"]["recomputed"]["verified_count"] = 0
    assert validate_price_batch_acceptance(tampered)["passed"] is False
    tampered = copy.deepcopy(payload)
    tampered["items"][0]["attempts"].append(copy.deepcopy(tampered["items"][0]["attempts"][0]))
    assert validate_price_batch_acceptance(tampered)["no_cross_product_attempt_binding"] is False

    markdown = render_price_batch_markdown(payload)
    for expected in ("本次批次通过", "艾莎 B级", "前价 `8.60`", "目标价 `8.80`", "数据库回读", "无跨商品副作用账本"):
        assert expected in markdown
    json_path = tmp_path / "任务12验收.json"
    markdown_path = tmp_path / "任务12报告.md"
    write_price_batch_reports(payload, json_path=json_path, markdown_path=markdown_path)
    assert json.loads(json_path.read_text(encoding="utf-8"))["batch"]["batch_id"] == batch.batch_id
    assert "艾莎" in markdown_path.read_text(encoding="utf-8")


def test_price_batch_web_exposes_only_audited_safe_controls(tmp_path):
    _, repository, _, _, batch, _, _ = _prepare(tmp_path)
    body = urlencode(
        {"batch_id": batch.batch_id, "action": "pause", "reason": "operator check"}
    ).encode("utf-8")
    environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "wsgi.input": io.BytesIO(body),
        "QUERY_STRING": "",
    }
    with (
        patch("app.web._runtime_db_for_request", return_value=str(repository.db_path)),
        patch("app.web._get_runtime_session_user", return_value="owner"),
    ):
        status, _, headers = web._handle_price_batches("POST", environ)
    assert status == "303 See Other"
    assert any(name == "Location" for name, _ in headers)
    events = repository.list_shadowbot_batch_control_events(batch.batch_id)
    assert len(events) == 1
    assert events[0]["action"] == "PAUSE"
    assert events[0]["actor"] == "owner"
    assert events[0]["applied"] is True

    query = urlencode({"batch_id": batch.batch_id})
    with (
        patch("app.web._runtime_db_for_request", return_value=str(repository.db_path)),
        patch("app.web._get_runtime_session_user", return_value="owner"),
    ):
        page = web._handle_price_batches("GET", {"REQUEST_METHOD": "GET", "QUERY_STRING": query})
    assert "任务12批次摘要" in page
    assert "艾莎" in page
    assert 'value="pause"' in page
    assert 'value="resume"' in page
    assert 'value="cancel_pending"' in page
    assert 'value="commit"' not in page
    assert 'value="retry"' not in page
    assert "/price-batches" in web.CSRF_PROTECTED_WRITE_PATHS

    invalid_body = urlencode(
        {"batch_id": batch.batch_id, "action": "commit", "reason": "must be rejected"}
    ).encode("utf-8")
    invalid_environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_LENGTH": str(len(invalid_body)),
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "wsgi.input": io.BytesIO(invalid_body),
        "QUERY_STRING": "",
    }
    with (
        patch("app.web._runtime_db_for_request", return_value=str(repository.db_path)),
        patch("app.web._get_runtime_session_user", return_value="owner"),
    ):
        invalid_status, _, _ = web._handle_price_batches("POST", invalid_environ)
    assert invalid_status == "422 Unprocessable Entity"
    assert len(repository.list_shadowbot_batch_control_events(batch.batch_id)) == 1

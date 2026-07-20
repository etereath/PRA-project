from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.enums import ReviewTaskStatus, TaskActionType, TaskStatus
from app.models import ReviewTask, Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_price_batch import (
    BatchItemStatus,
    BatchStatus,
    PriceBatchContractError,
    compute_batch_item_approved_payload_hash,
    normalize_price_batch_request,
)
from app.services.shadowbot_price_batch_orchestrator import ShadowBotPriceBatchOrchestrator


NOW = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
PLATFORM_KEY = "ant_flower_wechat"
PLATFORM_NAME = "蚂蚁花团供应商"


def _source_binding(count: int = 2) -> dict:
    products = [
        ("READ-ITEM-001", "艾莎", "B级", "8.60"),
        ("READ-ITEM-002", "卡布奇诺", "C级", "9.20"),
    ][:count]
    return {
        "source_read_batch_id": "READ-BATCH-T12-001",
        "source_snapshot_sha256": "sha256:" + "1" * 64,
        "source_page_context_sha256": "sha256:" + "2" * 64,
        "source_observed_at": NOW.isoformat(),
        "source_snapshot_max_age_seconds": 300,
        "platform": PLATFORM_KEY,
        "page_context": {"platform": PLATFORM_KEY, "platform_name": PLATFORM_NAME},
        "source_items": {
            item_id: {
                "item_id": item_id,
                "product_name": name,
                "grade": grade,
                "price": price,
                "listing_status": "ONLINE",
                "observed_at": NOW.isoformat(),
            }
            for item_id, name, grade, price in products
        },
    }


def _request(count: int = 2, *, mode: str = "FILL_PREVIEW") -> dict:
    raw_items = [
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
        },
        {
            "item_id": "ITEM-002",
            "ordinal": 2,
            "source_item_id": "READ-ITEM-002",
            "task_id": "TASK-002",
            "review_task_id": "REVIEW-002",
            "operation_id": "OP-002",
            "approved_payload_hash": "sha256:" + "0" * 64,
            "platform_sku": "SKU-CAPPUCCINO-C",
            "expected_product_name": "卡布奇诺",
            "expected_grade": "C级",
            "approved_expected_old_price": "9.20",
            "target_price": "9.50",
        },
    ][:count]
    return {
        "contract_version": 3,
        "batch_id": "PRICE-BATCH-T12-001",
        "platform": PLATFORM_KEY,
        "batch_type": "SERIAL_PRICE_UPDATE",
        "execution_mode": mode,
        "stop_policy": "PAUSE_ON_UNCERTAIN",
        "capture_evidence": False,
        "source_read_batch_id": "READ-BATCH-T12-001",
        "source_snapshot_sha256": "sha256:" + "1" * 64,
        "source_page_context_sha256": "sha256:" + "2" * 64,
        "source_observed_at": NOW.isoformat(),
        "source_snapshot_max_age_seconds": 300,
        "items": raw_items,
    }


def _authorize_request(repository: SQLiteRuntimeRepository, request: dict, source: dict) -> dict:
    initial = normalize_price_batch_request(request, source_binding=source, now=NOW)
    for raw_item, normalized_item in zip(request["items"], initial["items"], strict=True):
        raw_item["approved_payload_hash"] = compute_batch_item_approved_payload_hash(
            initial,
            normalized_item,
        )
    normalized = normalize_price_batch_request(request, source_binding=source, now=NOW)
    for item in normalized["items"]:
        task = Task(
            task_id=item["task_id"],
            internal_sku=item["platform_sku"],
            platform_name=PLATFORM_NAME,
            action_type=TaskActionType.UPDATE_PRICE,
            priority=100,
            task_status=TaskStatus.PENDING,
            created_at=NOW,
            target_price=Decimal(item["target_price"]),
            scope_type="sku",
            scope_key=item["platform_sku"],
            dedupe_key=f"{item['task_id']}|update_price",
        )
        repository.insert_task(task)
        review = ReviewTask(
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
            review_payload={},
            resolution_payload={
                "approved_payload_hash": item["approved_payload_hash"],
                "approved_execution_modes": [normalized["execution_mode"]],
                "approval_expires_at": (NOW + timedelta(hours=1)).isoformat(),
            },
            created_at=NOW,
            updated_at=NOW,
            resolved_by="owner",
            resolved_at=NOW,
        )
        repository.insert_review_tasks([review])
    return normalized


def _record_valid_fresh_read(orchestrator, batch_id: str, item, *, now: datetime = NOW):
    prices = {"ITEM-001": "8.60", "ITEM-002": "9.20"}
    orchestrator.record_fresh_read(
        batch_id,
        item.item_id,
        fresh_read_attempt_id=f"FRESH-{item.item_id}",
        result_sha256="sha256:" + ("7" if item.item_id == "ITEM-001" else "8") * 64,
        observed_product_name=item.expected_product_name,
        observed_grade=item.expected_grade,
        observed_platform_sku=item.external_platform_sku,
        observed_price=prices[item.item_id],
        observed_at=now,
        now=now,
    )


@pytest.fixture
def prepared(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    source = _source_binding()
    request = _request()
    normalized = _authorize_request(repository, request, source)
    orchestrator = ShadowBotPriceBatchOrchestrator(repository)
    batch = orchestrator.create_batch(request, source_binding=source, created_by="owner", now=NOW)
    return repository, orchestrator, request, normalized, batch


@pytest.fixture
def prepared_commit(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime-commit.sqlite3")
    repository.init_schema()
    source = _source_binding()
    request = _request(mode="COMMIT")
    normalized = _authorize_request(repository, request, source)
    orchestrator = ShadowBotPriceBatchOrchestrator(repository)
    batch = orchestrator.create_batch(request, source_binding=source, created_by="owner", now=NOW)
    return repository, orchestrator, request, normalized, batch


def test_create_batch_atomically_binds_each_approval_and_operation(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    source = _source_binding()
    request = _request()
    normalized = _authorize_request(repository, request, source)
    orchestrator = ShadowBotPriceBatchOrchestrator(repository)
    batch = orchestrator.create_batch(request, source_binding=source, created_by="owner", now=NOW)
    assert batch.status == BatchStatus.PENDING.value
    assert batch.pending_count == 2
    items = repository.list_shadowbot_batch_items(batch.batch_id)
    assert [item.ordinal for item in items] == [1, 2]
    assert [item.approved_payload_hash for item in items] == [
        item["approved_payload_hash"] for item in normalized["items"]
    ]
    assert repository.get_shadowbot_operation("OP-001").write_identity_key == items[0].write_identity_key


def test_approval_hash_mismatch_rolls_back_batch_and_operations(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    source = _source_binding()
    request = _request()
    _authorize_request(repository, request, source)
    request["items"][1]["target_price"] = "9.60"
    orchestrator = ShadowBotPriceBatchOrchestrator(repository)
    with pytest.raises(PriceBatchContractError) as caught:
        orchestrator.create_batch(request, source_binding=source, created_by="owner", now=NOW)
    assert caught.value.code == "APPROVED_PAYLOAD_HASH_MISMATCH"
    assert repository.get_shadowbot_batch(request["batch_id"]) is None
    assert repository.get_shadowbot_operation("OP-001") is None
    assert repository.get_shadowbot_operation("OP-002") is None


@pytest.mark.parametrize(
    ("resolution_patch", "expected_code"),
    [
        ({"approved_execution_modes": ["FILL_PREVIEW"]}, "APPROVAL_MODE_NOT_ALLOWED"),
        ({"approval_expires_at": (NOW - timedelta(seconds=1)).isoformat()}, "APPROVAL_EXPIRED"),
    ],
)
def test_commit_requires_current_per_item_commit_approval(
    tmp_path,
    resolution_patch,
    expected_code,
):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    source = _source_binding(count=1)
    request = _request(count=1, mode="COMMIT")
    _authorize_request(repository, request, source)
    review = repository.get_review_task("REVIEW-001")
    review.resolution_payload.update(resolution_patch)
    repository.update_review_task(review)
    orchestrator = ShadowBotPriceBatchOrchestrator(repository)
    with pytest.raises(PriceBatchContractError) as caught:
        orchestrator.create_batch(request, source_binding=source, created_by="owner", now=NOW)
    assert caught.value.code == expected_code
    assert repository.get_shadowbot_batch(request["batch_id"]) is None


def test_items_are_claimed_strictly_serial_and_counts_finish(prepared):
    repository, orchestrator, _, _, batch = prepared
    first = orchestrator.claim_next(batch.batch_id, now=NOW)
    assert first.item_id == "ITEM-001"
    assert orchestrator.claim_next(batch.batch_id, now=NOW) is None
    _record_valid_fresh_read(orchestrator, batch.batch_id, first)
    orchestrator.bind_execution_attempt(
        batch.batch_id,
        first.item_id,
        execution_attempt_id="ATTEMPT-001",
        now=NOW,
    )
    orchestrator.record_item_result(
        batch.batch_id,
        first.item_id,
        status=BatchItemStatus.PREVIEWED.value,
        execution_attempt_id="ATTEMPT-001",
        post_commit_price=Decimal("8.60"),
        result_id="RESULT-001",
        result_hash="sha256:" + "a" * 64,
        now=NOW,
    )
    second = orchestrator.claim_next(batch.batch_id, now=NOW)
    assert second.item_id == "ITEM-002"
    _record_valid_fresh_read(orchestrator, batch.batch_id, second)
    orchestrator.bind_execution_attempt(
        batch.batch_id,
        second.item_id,
        execution_attempt_id="ATTEMPT-002",
        now=NOW,
    )
    orchestrator.record_item_result(
        batch.batch_id,
        second.item_id,
        status=BatchItemStatus.PREVIEWED.value,
        execution_attempt_id="ATTEMPT-002",
        post_commit_price=Decimal("9.20"),
        result_id="RESULT-002",
        result_hash="sha256:" + "b" * 64,
        now=NOW,
    )
    stored = repository.get_shadowbot_batch(batch.batch_id)
    assert stored.status == BatchStatus.COMPLETED.value
    assert stored.processed_count == stored.previewed_count == 2
    assert stored.pending_count == stored.ready_count == stored.running_count == 0


def test_fresh_read_old_price_drift_is_persisted_and_blocks_write(prepared):
    repository, orchestrator, _, _, batch = prepared
    item = orchestrator.claim_next(batch.batch_id, now=NOW)
    with pytest.raises(PriceBatchContractError) as caught:
        orchestrator.record_fresh_read(
            batch.batch_id,
            item.item_id,
            fresh_read_attempt_id="READ-FRESH-001",
            result_sha256="sha256:" + "c" * 64,
            observed_product_name="艾莎",
            observed_grade="B级",
            observed_platform_sku="SKU-AISHA-B",
            observed_price="8.70",
            observed_at=NOW,
            now=NOW,
        )
    assert caught.value.code == "OLD_PRICE_CHANGED"
    stored = repository.get_shadowbot_batch_item(batch.batch_id, item.item_id)
    assert stored.status == BatchItemStatus.FAILED.value
    assert stored.error_code == "OLD_PRICE_CHANGED"
    assert orchestrator.claim_next(batch.batch_id, now=NOW).item_id == "ITEM-002"


def test_fresh_read_older_than_sixty_seconds_is_rejected(prepared):
    repository, orchestrator, _, _, batch = prepared
    item = orchestrator.claim_next(batch.batch_id, now=NOW)
    with pytest.raises(PriceBatchContractError) as caught:
        orchestrator.record_fresh_read(
            batch.batch_id,
            item.item_id,
            fresh_read_attempt_id="READ-FRESH-STALE-001",
            result_sha256="sha256:" + "c" * 64,
            observed_product_name="艾莎",
            observed_grade="B级",
            observed_platform_sku="SKU-AISHA-B",
            observed_price="8.60",
            observed_at=NOW - timedelta(seconds=61),
            now=NOW,
        )
    assert caught.value.code == "FRESH_READ_EXPIRED"
    assert repository.get_shadowbot_batch_item(batch.batch_id, item.item_id).error_code == "FRESH_READ_EXPIRED"


def test_diagnostic_preview_still_requires_persisted_fresh_read(prepared):
    repository, orchestrator, _, _, batch = prepared
    item = orchestrator.claim_next(batch.batch_id, now=NOW)
    with pytest.raises(PriceBatchContractError) as caught:
        orchestrator.bind_execution_attempt(
            batch.batch_id,
            item.item_id,
            execution_attempt_id="ATTEMPT-NO-FRESH-001",
            now=NOW,
        )
    assert caught.value.code == "FRESH_READ_EXPIRED"
    stored = repository.get_shadowbot_batch_item(batch.batch_id, item.item_id)
    assert stored.status == BatchItemStatus.FAILED.value


def test_commit_attempt_starts_without_persisted_fresh_read(prepared_commit):
    repository, orchestrator, _, _, batch = prepared_commit
    item = orchestrator.claim_next(batch.batch_id, now=NOW)
    orchestrator.bind_execution_attempt(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="ATTEMPT-DIRECT-COMMIT-001",
        now=NOW,
    )
    stored = repository.get_shadowbot_batch_item(batch.batch_id, item.item_id)
    assert stored.status == BatchItemStatus.RUNNING.value
    assert stored.fresh_read_attempt_id == ""
    assert stored.fresh_old_price is None
    assert stored.current_execution_attempt_id == "ATTEMPT-DIRECT-COMMIT-001"


def test_preview_post_read_must_still_equal_fresh_old_price(prepared):
    repository, orchestrator, _, _, batch = prepared
    item = orchestrator.claim_next(batch.batch_id, now=NOW)
    _record_valid_fresh_read(orchestrator, batch.batch_id, item)
    orchestrator.bind_execution_attempt(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="ATTEMPT-PREVIEW-DRIFT",
        now=NOW,
    )
    with pytest.raises(PriceBatchContractError) as caught:
        orchestrator.record_item_result(
            batch.batch_id,
            item.item_id,
            status=BatchItemStatus.PREVIEWED.value,
            execution_attempt_id="ATTEMPT-PREVIEW-DRIFT",
            post_commit_price=Decimal("8.80"),
            result_id="RESULT-PREVIEW-DRIFT",
            result_hash="sha256:" + "f" * 64,
            now=NOW,
        )
    assert caught.value.code == "PREVIEW_INPUT_MISMATCH"
    assert repository.get_shadowbot_batch_item(batch.batch_id, item.item_id).status == "FAILED"


def test_approval_expiring_after_batch_creation_fails_claim_without_stuck_running_item(prepared):
    repository, orchestrator, _, _, batch = prepared
    review = repository.get_review_task("REVIEW-001")
    review.resolution_payload["approval_expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    repository.update_review_task(review)
    with pytest.raises(PriceBatchContractError) as caught:
        orchestrator.claim_next(batch.batch_id, now=NOW)
    assert caught.value.code == "APPROVAL_EXPIRED"
    item = repository.get_shadowbot_batch_item(batch.batch_id, "ITEM-001")
    assert item.status == BatchItemStatus.FAILED.value
    assert item.error_code == "APPROVAL_EXPIRED"


def test_stop_before_claim_and_after_submit_boundary(prepared_commit):
    repository, orchestrator, _, _, batch = prepared_commit
    assert orchestrator.request_safe_stop(batch.batch_id, now=NOW) == "PAUSED_BEFORE_NEXT_ITEM"
    assert repository.get_shadowbot_batch(batch.batch_id).status == BatchStatus.PAUSED.value
    assert orchestrator.resume(batch.batch_id, actor="owner", now=NOW)
    first = orchestrator.claim_next(batch.batch_id, now=NOW)
    orchestrator.bind_execution_attempt(
        batch.batch_id,
        first.item_id,
        execution_attempt_id="ATTEMPT-COMMIT-001",
        now=NOW,
    )
    assert orchestrator.request_safe_stop(
        batch.batch_id,
        item_id=first.item_id,
        side_effect_state="SUBMIT_INTENT_RECORDED",
        now=NOW,
    ) == "DEFERRED_UNTIL_RESULT"
    assert repository.get_shadowbot_batch_item(batch.batch_id, first.item_id).status == "RUNNING"
    orchestrator.record_item_result(
        batch.batch_id,
        first.item_id,
        status=BatchItemStatus.VERIFIED.value,
        execution_attempt_id="ATTEMPT-COMMIT-001",
        post_commit_price=Decimal("8.80"),
        result_id="RESULT-COMMIT-001",
        result_hash="sha256:" + "d" * 64,
        stop_requested=True,
        now=NOW,
    )
    stored = repository.get_shadowbot_batch(batch.batch_id)
    assert stored.status == BatchStatus.PAUSED.value
    assert stored.pending_count == 1


def test_commit_verified_price_mismatch_routes_to_reconcile(prepared_commit):
    repository, orchestrator, _, _, batch = prepared_commit
    item = orchestrator.claim_next(batch.batch_id, now=NOW)
    orchestrator.bind_execution_attempt(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="ATTEMPT-COMMIT-DRIFT",
        now=NOW,
    )
    with pytest.raises(PriceBatchContractError) as caught:
        orchestrator.record_item_result(
            batch.batch_id,
            item.item_id,
            status=BatchItemStatus.VERIFIED.value,
            execution_attempt_id="ATTEMPT-COMMIT-DRIFT",
            post_commit_price=Decimal("8.70"),
            result_id="RESULT-COMMIT-DRIFT",
            result_hash="sha256:" + "9" * 64,
            now=NOW,
        )
    assert caught.value.code == "SUBMIT_RESULT_UNKNOWN"
    stored = repository.get_shadowbot_batch_item(batch.batch_id, item.item_id)
    assert stored.status == BatchItemStatus.NEEDS_RECONCILIATION.value
    assert repository.get_shadowbot_batch(batch.batch_id).status == BatchStatus.PAUSED.value


def test_stop_before_submit_is_terminal_without_touching_next_item(prepared):
    repository, orchestrator, _, _, batch = prepared
    first = orchestrator.claim_next(batch.batch_id, now=NOW)
    assert orchestrator.request_safe_stop(
        batch.batch_id,
        item_id=first.item_id,
        side_effect_state="NOT_STARTED",
        now=NOW,
    ) == "STOPPED_BEFORE_SUBMIT"
    items = repository.list_shadowbot_batch_items(batch.batch_id)
    assert items[0].status == BatchItemStatus.FAILED.value
    assert items[0].error_code == "WORKER_STOP_REQUESTED"
    assert items[1].status == BatchItemStatus.PENDING.value
    assert repository.get_shadowbot_batch(batch.batch_id).status == BatchStatus.PAUSED.value


def test_unknown_pauses_and_allows_only_one_reconcile(prepared_commit):
    repository, orchestrator, _, _, batch = prepared_commit
    item = orchestrator.claim_next(batch.batch_id, now=NOW)
    orchestrator.bind_execution_attempt(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="ATTEMPT-UNKNOWN-001",
        now=NOW,
    )
    orchestrator.record_item_result(
        batch.batch_id,
        item.item_id,
        status=BatchItemStatus.NEEDS_RECONCILIATION.value,
        execution_attempt_id="ATTEMPT-UNKNOWN-001",
        error_code="SUBMIT_RESULT_UNKNOWN",
        result_id="RESULT-UNKNOWN-001",
        result_hash="sha256:" + "e" * 64,
        now=NOW,
    )
    assert repository.get_shadowbot_batch(batch.batch_id).status == BatchStatus.PAUSED.value
    claimed = orchestrator.claim_reconcile(
        batch.batch_id,
        item.item_id,
        reconcile_attempt_id="RECONCILE-001",
        now=NOW,
    )
    assert claimed.reconcile_attempt_id == "RECONCILE-001"
    with pytest.raises(PriceBatchContractError) as caught:
        orchestrator.claim_reconcile(
            batch.batch_id,
            item.item_id,
            reconcile_attempt_id="RECONCILE-002",
            now=NOW,
        )
    assert caught.value.code == "RECONCILIATION_CONFLICT"
    assert orchestrator.complete_reconcile(
        batch.batch_id,
        item.item_id,
        reconcile_attempt_id="RECONCILE-001",
        outcome="VERIFIED",
        post_commit_price=Decimal("8.80"),
        now=NOW,
    )
    assert not orchestrator.complete_reconcile(
        batch.batch_id,
        item.item_id,
        reconcile_attempt_id="RECONCILE-001",
        outcome="VERIFIED",
        post_commit_price=Decimal("8.80"),
        now=NOW,
    )
    stored = repository.get_shadowbot_batch(batch.batch_id)
    assert stored.reconciled_item_count == 1
    assert stored.needs_reconciliation_count == 0


def test_unresolved_reconcile_blocks_resume(prepared_commit):
    _, orchestrator, _, _, batch = prepared_commit
    item = orchestrator.claim_next(batch.batch_id, now=NOW)
    orchestrator.record_item_result(
        batch.batch_id,
        item.item_id,
        status=BatchItemStatus.NEEDS_RECONCILIATION.value,
        execution_attempt_id="ATTEMPT-UNKNOWN-RESUME",
        error_code="SUBMIT_RESULT_UNKNOWN",
        now=NOW,
    )
    with pytest.raises(PriceBatchContractError) as caught:
        orchestrator.resume(batch.batch_id, actor="owner", now=NOW)
    assert caught.value.code == "RECONCILIATION_REQUIRED"


def test_cancel_pending_never_cancels_running_item(prepared):
    repository, orchestrator, _, _, batch = prepared
    first = orchestrator.claim_next(batch.batch_id, now=NOW)
    assert orchestrator.cancel_pending(batch.batch_id, actor="owner", reason="abort remainder", now=NOW)
    items = repository.list_shadowbot_batch_items(batch.batch_id)
    assert items[0].status == BatchItemStatus.RUNNING.value
    assert items[1].status == BatchItemStatus.CANCELLED.value
    assert first.item_id == items[0].item_id


def test_recovery_never_replays_commit_and_routes_unknown_to_reconcile(prepared_commit):
    repository, orchestrator, _, _, batch = prepared_commit
    item = orchestrator.claim_next(batch.batch_id, now=NOW)
    orchestrator.bind_execution_attempt(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="ATTEMPT-INTERRUPTED-001",
        now=NOW,
    )
    assert orchestrator.recover_batch(batch.batch_id, worker_stopped=False, now=NOW) == []
    assert repository.get_shadowbot_batch_item(batch.batch_id, item.item_id).status == "RUNNING"
    assert orchestrator.recover_batch(batch.batch_id, worker_stopped=True, now=NOW) == [item.item_id]
    recovered = repository.get_shadowbot_batch_item(batch.batch_id, item.item_id)
    assert recovered.status == BatchItemStatus.NEEDS_RECONCILIATION.value
    assert recovered.error_code == "SUBMIT_RESULT_UNKNOWN"


def test_recovery_projects_already_verified_operation_without_new_attempt(prepared_commit):
    repository, orchestrator, _, _, batch = prepared_commit
    item = orchestrator.claim_next(batch.batch_id, now=NOW)
    orchestrator.bind_execution_attempt(
        batch.batch_id,
        item.item_id,
        execution_attempt_id="ATTEMPT-VERIFIED-001",
        now=NOW,
    )
    repository.update_shadowbot_operation_status(item.operation_id, "VERIFIED")
    before = repository.list_shadowbot_execution_attempts(operation_id=item.operation_id)
    assert orchestrator.recover_batch(batch.batch_id, worker_stopped=True, now=NOW) == [item.item_id]
    after = repository.list_shadowbot_execution_attempts(operation_id=item.operation_id)
    assert before == after == []
    assert repository.get_shadowbot_batch_item(batch.batch_id, item.item_id).status == "VERIFIED"

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from app.enums import (
    ReviewTaskStatus,
    TaskActionType,
    TaskOriginType,
    TaskStatus,
)
from app.exceptions import ValidationError
from app.listing_identity import listing_identity_key
from app.models import ReviewTask, Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_executor import (
    ShadowBotFileQueueRunner,
    ShadowBotStartBoundaryError,
)
from app.services.shadowbot_listing_action_contract import (
    compute_listing_result_hash,
)
from app.services.shadowbot_listing_action_pipeline import (
    _project_verified_listing,
    _recompute_listing_batch_counts,
    ensure_listing_action_reconcile_attempt,
    import_listing_action_result,
    propose_listing_action_batch,
    publish_listing_action_batch,
)
from app.services.shadowbot_queue import (
    ShadowBotQueueWatchdog,
    ShadowBotResultImporter,
)
from app.services.shadowbot_listing_sync import import_listing_sync_result
from app.services.workflow import _load_latest_platform_observations
from app.shadowbot_listing_contract import (
    derive_v5_batch_semantics,
    v5_result_counts,
)
from shadowbot.test2 import shadowbot_queue_worker
from tests.test_shadowbot_listing_sync import (
    PLATFORM,
    _item,
    _mapping_file,
    _repository,
    _request,
    _result,
)


def _seed_waiting_snapshot(
    repository: SQLiteRuntimeRepository,
    tmp_path: Path,
) -> None:
    request = _request(
        repository,
        tmp_path,
        batch_id="BATCH-SYNC-WRITE-01",
        attempt_id="ATTEMPT-SYNC-WRITE-01",
    )
    result = _result(
        request,
        locations={
            "SKU-ONLINE-001": "online_only",
            "SKU-WAITING-001": "waiting_only",
        },
        scan_started_at=datetime.now(UTC).isoformat(),
    )
    result["snapshot"]["scan_completed_at"] = datetime.now(UTC).isoformat()
    result["snapshot"]["online_scan_started_at"] = result["snapshot"][
        "scan_started_at"
    ]
    result["snapshot"]["online_scan_completed_at"] = result["snapshot"][
        "scan_started_at"
    ]
    result["snapshot"]["waiting_scan_started_at"] = result["snapshot"][
        "scan_started_at"
    ]
    result["snapshot"]["waiting_scan_completed_at"] = result["snapshot"][
        "scan_completed_at"
    ]
    result["ended_at"] = result["snapshot"]["scan_completed_at"]
    result["result_payload_sha256"] = compute_listing_result_hash(result)
    import_listing_sync_result(
        repository,
        request=request,
        result=result,
        result_file_sha256="a" * 64,
        source_result_path="test.result.json",
    )


def _insert_set_online_task(
    repository: SQLiteRuntimeRepository,
    *,
    task_id: str = "TASK-SET-ONLINE-0001",
) -> Task:
    now = datetime.now(UTC)
    task = Task(
        task_id=task_id,
        internal_sku="SKU-WAITING-001",
        platform_name=PLATFORM,
        action_type=TaskActionType.SET_ONLINE,
        priority=5,
        task_status=TaskStatus.PENDING,
        created_at=now,
        origin_type=TaskOriginType.MANUAL,
        expected_old_price=Decimal("21.00"),
        target_price=Decimal("22.00"),
        target_inventory=8,
        target_status="online",
        scope_type="sku",
        scope_key="SKU-WAITING-001",
        dedupe_key="test-set-online-" + task_id.lower(),
        required_by=now + timedelta(hours=2),
        expires_at=now + timedelta(hours=2),
        updated_at=now,
    )
    repository.insert_task(task)
    return task


def _insert_set_offline_task(
    repository: SQLiteRuntimeRepository,
    *,
    task_id: str,
    internal_sku: str,
) -> Task:
    now = datetime.now(UTC)
    task = Task(
        task_id=task_id,
        internal_sku=internal_sku,
        platform_name=PLATFORM,
        action_type=TaskActionType.SET_OFFLINE,
        priority=5,
        task_status=TaskStatus.PENDING,
        created_at=now,
        origin_type=TaskOriginType.MANUAL,
        expected_old_price=Decimal("21.00"),
        target_price=None,
        target_inventory=None,
        target_status="offline",
        scope_type="sku",
        scope_key=internal_sku,
        dedupe_key="test-set-offline-" + task_id.lower(),
        required_by=now + timedelta(hours=2),
        expires_at=now + timedelta(hours=2),
        updated_at=now,
    )
    repository.insert_task(task)
    return task


def _write_result(request: dict, *, request_file_sha256: str) -> dict:
    request_item = request["items"][0]
    item = {
        name: request_item[name]
        for name in (
            "source_task_id",
            "operation_id",
            "item_execution_attempt_id",
            "internal_sku",
            "item_payload_sha256",
        )
    }
    item.update(
        {
            "operation_result": "VERIFIED",
            "detail_effect_state": "VERIFIED",
            "listing_effect_state": "VERIFIED",
            "detail_save_clicked": True,
            "action_confirm_clicked": True,
            "observed_price_before_action": "21.00",
            "observed_inventory_before_action": 8,
            "observed_price_after_detail_save": "22.00",
            "observed_inventory_after_detail_save": 8,
            "detail_save_clicked_at": datetime.now(UTC).isoformat(),
            "action_clicked_at": datetime.now(UTC).isoformat(),
            "readback_observed_at": datetime.now(UTC).isoformat(),
            "actual_price": "22.00",
            "actual_inventory": 8,
            "error_code": "",
            "error_message": "",
        }
    )
    counts = v5_result_counts([item])
    result = {
        "schema_version": "shadowbot-listing-action-batch-result-1.0",
        "contract_version": 5,
        "action_type": "set_online",
        "batch_id": request["batch_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "execution_mode": "COMMIT",
        "manifest_sha256": request["manifest_sha256"],
        "instruction_hash": request["instruction_hash"],
        "request_file_sha256": "sha256:" + request_file_sha256,
        "result_id": "RESULT-SET-ONLINE-0001",
        "started_at": datetime.now(UTC).isoformat(),
        "ended_at": datetime.now(UTC).isoformat(),
        "items": [item],
        "counts": counts,
        **derive_v5_batch_semantics(counts),
    }
    result["result_payload_sha256"] = compute_listing_result_hash(result)
    return result


def _insert_pending_review(
    repository: SQLiteRuntimeRepository,
    *,
    review_task_id: str,
    internal_sku: str = "SKU-WAITING-001",
    blocked_actions: object = None,
    status: ReviewTaskStatus = ReviewTaskStatus.PENDING,
) -> None:
    now = datetime.now(UTC)
    payload = (
        {}
        if blocked_actions is None
        else {
            "blocked_actions": blocked_actions,
            "reason_code": "LISTING_DATA_MISMATCH",
        }
    )
    repository.insert_review_tasks(
        [
            ReviewTask(
                review_task_id=review_task_id,
                trade_date=None,
                scope_type="sku",
                scope_key=internal_sku,
                dedupe_key=review_task_id,
                source_task_id=None,
                review_type="listing_data_mismatch",
                review_status=status,
                internal_sku=internal_sku,
                platform_name=PLATFORM,
                reason="pending review",
                review_payload=payload,
                created_at=now,
                updated_at=now,
            )
        ]
    )


def test_proposal_uses_latest_waiting_snapshot_without_persisting_batch(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _seed_waiting_snapshot(repository, tmp_path)
    _insert_set_online_task(repository)

    proposal = propose_listing_action_batch(
        repository,
        batch_id="BATCH-SET-ONLINE-0001",
        task_ids=["TASK-SET-ONLINE-0001"],
        mapping_path=_mapping_file(tmp_path / "mapping-write.json"),
    )

    assert proposal["publishable"] is True
    assert proposal["gate_items"][0]["listing_location"] == "waiting_only"
    assert proposal["gate_items"][0]["observed_inventory"] == 8
    with repository.connect_read() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM shadowbot_listing_action_batches "
            "WHERE batch_id = 'BATCH-SET-ONLINE-0001'"
        ).fetchone()[0]
    assert count == 0


def test_pending_review_task_blocks_all_listing_writes(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _seed_waiting_snapshot(repository, tmp_path)
    _insert_set_online_task(repository)
    _insert_pending_review(
        repository,
        review_task_id="REVIEW-PENDING-MISMATCH-0001",
        blocked_actions=["set_online", "set_offline", "update_price"],
    )

    proposal = propose_listing_action_batch(
        repository,
        batch_id="BATCH-SET-ONLINE-REVIEW-BLOCKED-0001",
        task_ids=["TASK-SET-ONLINE-0001"],
        mapping_path=_mapping_file(tmp_path / "mapping-review.json"),
    )

    assert proposal["publishable"] is False
    assert proposal["gate_items"][0]["decision"] == "BLOCKED"
    assert "LISTING_DATA_MISMATCH" in proposal["gate_items"][0]["block_reasons"]


def test_cancelled_review_task_does_not_block_listing_write(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _seed_waiting_snapshot(repository, tmp_path)
    _insert_set_online_task(repository)
    _insert_pending_review(
        repository,
        review_task_id="REVIEW-CANCELLED-MISMATCH-0001",
        blocked_actions=["set_online"],
        status=ReviewTaskStatus.CANCELLED,
    )

    proposal = propose_listing_action_batch(
        repository,
        batch_id="BATCH-SET-ONLINE-REVIEW-CANCELLED-0001",
        task_ids=["TASK-SET-ONLINE-0001"],
        mapping_path=_mapping_file(tmp_path / "mapping-review-cancelled.json"),
    )

    assert proposal["publishable"] is True


def test_multiple_pending_reviews_take_blocked_action_union_and_fail_closed(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _seed_waiting_snapshot(repository, tmp_path)
    _insert_set_online_task(repository)
    _insert_pending_review(
        repository,
        review_task_id="REVIEW-UNION-ONLINE-0001",
        blocked_actions=["set_online"],
    )
    _insert_pending_review(
        repository,
        review_task_id="REVIEW-UNION-OFFLINE-0001",
        blocked_actions=["set_offline"],
    )

    proposal = propose_listing_action_batch(
        repository,
        batch_id="BATCH-SET-ONLINE-REVIEW-UNION-0001",
        task_ids=["TASK-SET-ONLINE-0001"],
        mapping_path=_mapping_file(tmp_path / "mapping-review-union.json"),
    )
    assert proposal["publishable"] is False
    assert "LISTING_DATA_MISMATCH" in proposal["gate_items"][0]["block_reasons"]

    repository2 = _repository(tmp_path / "malformed")
    _seed_waiting_snapshot(repository2, tmp_path / "malformed")
    _insert_set_online_task(repository2)
    _insert_pending_review(
        repository2,
        review_task_id="REVIEW-MALFORMED-0001",
        blocked_actions=None,
    )
    malformed = propose_listing_action_batch(
        repository2,
        batch_id="BATCH-SET-ONLINE-REVIEW-MALFORMED-0001",
        task_ids=["TASK-SET-ONLINE-0001"],
        mapping_path=_mapping_file(tmp_path / "mapping-review-malformed.json"),
    )
    assert malformed["publishable"] is False
    assert "REVIEW_BLOCKED_ACTIONS_INVALID" in malformed["gate_items"][0][
        "block_reasons"
    ]


def test_review_created_after_proposal_is_rechecked_inside_publish_transaction(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _seed_waiting_snapshot(repository, tmp_path)
    _insert_set_online_task(repository)
    mapping_path = _mapping_file(tmp_path / "mapping-review-toctou.json")
    proposal = propose_listing_action_batch(
        repository,
        batch_id="BATCH-SET-ONLINE-REVIEW-TOCTOU-0001",
        task_ids=["TASK-SET-ONLINE-0001"],
        mapping_path=mapping_path,
    )
    _insert_pending_review(
        repository,
        review_task_id="REVIEW-TOCTOU-MISMATCH-0001",
        blocked_actions=["set_online"],
    )

    with pytest.raises(ValidationError, match="未解决的写入 Review"):
        publish_listing_action_batch(
            repository,
            ShadowBotFileQueueRunner(tmp_path / "queue-review-toctou"),
            proposal=proposal,
            applet_uri="weixin://launchapplet/test",
            confirmation_text=proposal["required_confirmation"],
            confirmed_by="tester",
            execution_attempt_id="ATTEMPT-SET-ONLINE-REVIEW-TOCTOU-0001",
        )

    with repository.connect_read() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM shadowbot_listing_action_batches "
            "WHERE batch_id = ?",
            (proposal["manifest"]["batch_id"],),
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("platform_name", "其他平台"),
        ("target_price", "23.00"),
        ("target_inventory", 9),
        ("target_status", "offline"),
        (
            "expires_at",
            (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        ),
    ],
)
def test_task_execution_payload_is_rechecked_inside_publish_transaction(
    tmp_path: Path,
    changed_field: str,
    changed_value: object,
) -> None:
    repository = _repository(tmp_path)
    _seed_waiting_snapshot(repository, tmp_path)
    _insert_set_online_task(repository)
    proposal = propose_listing_action_batch(
        repository,
        batch_id=(
            "BATCH-SET-ONLINE-TASK-TOCTOU-"
            + changed_field.replace("_", "-").upper()
        ),
        task_ids=["TASK-SET-ONLINE-0001"],
        mapping_path=_mapping_file(
            tmp_path / f"mapping-task-toctou-{changed_field}.json"
        ),
    )
    with repository.connect_write() as connection, connection:
        connection.execute(
            f"UPDATE tasks SET {changed_field} = ? WHERE task_id = ?",
            (changed_value, "TASK-SET-ONLINE-0001"),
        )

    queue_dir = tmp_path / f"queue-task-toctou-{changed_field}"
    with pytest.raises(ValidationError, match="执行载荷已变化"):
        publish_listing_action_batch(
            repository,
            ShadowBotFileQueueRunner(queue_dir),
            proposal=proposal,
            applet_uri="weixin://launchapplet/test",
            confirmation_text=proposal["required_confirmation"],
            confirmed_by="tester",
            execution_attempt_id=(
                "ATTEMPT-SET-ONLINE-TASK-TOCTOU-"
                + changed_field.replace("_", "-").upper()
            ),
        )

    batch_id = proposal["manifest"]["batch_id"]
    item = proposal["manifest"]["items"][0]
    with repository.connect_read() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM shadowbot_batch_registry "
            "WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM shadowbot_listing_action_batches "
            "WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM shadowbot_listing_action_batch_items "
            "WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM shadowbot_operations "
            "WHERE operation_id = ?",
            (item["operation_id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM shadowbot_execution_attempts "
            "WHERE operation_id = ?",
            (item["operation_id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM shadowbot_write_locks "
            "WHERE operation_id = ?",
            (item["operation_id"],),
        ).fetchone()[0] == 0
    assert not list((queue_dir / "inbox").glob("*.json"))


@pytest.mark.parametrize(
    ("outcomes", "expected_status"),
    [
        (["NOT_ATTEMPTED"], "FAILED"),
        (["NOT_APPLIED", "NOT_ATTEMPTED"], "FAILED"),
        (["VERIFIED", "NOT_ATTEMPTED"], "PARTIAL"),
        (["VERIFIED", "NEEDS_RECONCILIATION"], "UNKNOWN"),
        (["PARTIALLY_APPLIED", "NOT_ATTEMPTED"], "PARTIAL"),
    ],
)
def test_pipeline_batch_recompute_uses_shared_v5_terminal_semantics(
    outcomes: list[str],
    expected_status: str,
) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.execute(
            """
            CREATE TABLE shadowbot_listing_action_batch_items(
                batch_id TEXT NOT NULL,
                operation_result TEXT
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO shadowbot_listing_action_batch_items(
                batch_id, operation_result
            ) VALUES ('BATCH-SEMANTICS', ?)
            """,
            [(outcome,) for outcome in outcomes],
        )

        counts = _recompute_listing_batch_counts(
            connection,
            "BATCH-SEMANTICS",
        )

        assert counts["batch_status"] == expected_status
        assert (
            counts["verified_count"]
            + counts["unknown_count"]
            + counts["partial_effect_count"]
            + counts["not_attempted_count"]
            + counts["failed_count"]
            == counts["batch_target_count"]
        )
    finally:
        connection.close()


def test_development_publish_requires_exact_confirmation(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _seed_waiting_snapshot(repository, tmp_path)
    _insert_set_online_task(repository)
    mapping_path = _mapping_file(tmp_path / "mapping-write.json")
    proposal = propose_listing_action_batch(
        repository,
        batch_id="BATCH-SET-ONLINE-0002",
        task_ids=["TASK-SET-ONLINE-0001"],
        mapping_path=mapping_path,
    )
    runner = ShadowBotFileQueueRunner(tmp_path / "queue")

    with pytest.raises(ValidationError, match="确认文本"):
        publish_listing_action_batch(
            repository,
            runner,
            proposal=proposal,
            applet_uri="weixin://launchapplet/test",
            confirmation_text="错误确认",
            confirmed_by="tester",
        )
    with repository.connect_read() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM shadowbot_listing_action_batches "
            "WHERE batch_id = 'BATCH-SET-ONLINE-0002'"
        ).fetchone()[0]
    assert count == 0


@pytest.mark.parametrize("published", [False, True])
def test_publish_boundary_closes_v5_accounting_for_every_item(
    tmp_path: Path,
    published: bool,
) -> None:
    repository = _repository(tmp_path)
    _seed_waiting_snapshot(repository, tmp_path)
    task = _insert_set_online_task(
        repository,
        task_id=f"TASK-SET-ONLINE-BOUNDARY-{int(published)}",
    )
    proposal = propose_listing_action_batch(
        repository,
        batch_id=f"BATCH-SET-ONLINE-BOUNDARY-{int(published)}",
        task_ids=[task.task_id],
        mapping_path=_mapping_file(tmp_path / f"mapping-boundary-{int(published)}.json"),
    )

    class BoundaryRunner:
        def __init__(self) -> None:
            self.queue_dir = tmp_path / f"queue-boundary-{int(published)}"
            for name in ("inbox", "working", "results"):
                (self.queue_dir / name).mkdir(parents=True, exist_ok=True)

        def start(self, request: dict) -> ShadowBotStartResult:
            raise ShadowBotStartBoundaryError(
                "controlled publish boundary",
                published=published,
            )

    with pytest.raises(ShadowBotStartBoundaryError):
        publish_listing_action_batch(
            repository,
            BoundaryRunner(),
            proposal=proposal,
            applet_uri="weixin://launchapplet/test",
            confirmation_text=proposal["required_confirmation"],
            confirmed_by="tester",
            execution_attempt_id=f"ATTEMPT-SET-ONLINE-BOUNDARY-{int(published)}",
        )

    item_id = proposal["manifest"]["items"][0]["item_id"]
    operation_id = proposal["manifest"]["items"][0]["operation_id"]
    with repository.connect_read() as connection:
        batch = connection.execute(
            "SELECT status, unknown_count, not_attempted_count "
            "FROM shadowbot_listing_action_batches WHERE batch_id = ?",
            (proposal["manifest"]["batch_id"],),
        ).fetchone()
        item = connection.execute(
            "SELECT operation_result, detail_effect_state, listing_effect_state, "
            "item_execution_attempt_id "
            "FROM shadowbot_listing_action_batch_items WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        attempt_id = item["item_execution_attempt_id"]
        operation = connection.execute(
            "SELECT status, operation_result FROM shadowbot_operations "
            "WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        attempt = connection.execute(
            "SELECT status, side_effect_state, ended_at "
            "FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        lock = connection.execute(
            "SELECT status FROM shadowbot_write_locks WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()

    if published:
        assert batch["status"] == "UNKNOWN"
        assert batch["unknown_count"] == 1
        assert item["operation_result"] == "NEEDS_RECONCILIATION"
        assert operation["status"] == "NEEDS_RECONCILIATION"
        assert operation["operation_result"] == "NEEDS_RECONCILIATION"
        assert attempt["status"] == "START_UNKNOWN"
        assert attempt["side_effect_state"] == "UNKNOWN"
        assert lock["status"] == "UNKNOWN"
    else:
        assert batch["status"] == "FAILED"
        assert batch["not_attempted_count"] == 1
        assert item["operation_result"] == "NOT_ATTEMPTED"
        assert operation["status"] == "PENDING"
        assert operation["operation_result"] == "NOT_ATTEMPTED"
        assert attempt["status"] == "START_FAILED"
        assert attempt["side_effect_state"] == "NOT_STARTED"
        assert lock["status"] == "RELEASED"
    assert item["detail_effect_state"] == "NOT_STARTED"
    assert item["listing_effect_state"] == "NOT_STARTED"
    assert attempt["ended_at"] is not None


def test_publish_reuses_released_write_lock_for_next_batch(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _seed_waiting_snapshot(repository, tmp_path)
    first_task = _insert_set_online_task(
        repository,
        task_id="TASK-SET-ONLINE-RELEASED-0001",
    )
    mapping_path = _mapping_file(tmp_path / "mapping-write.json")
    first = propose_listing_action_batch(
        repository,
        batch_id="BATCH-SET-ONLINE-RELEASED-0001",
        task_ids=[first_task.task_id],
        mapping_path=mapping_path,
    )
    publish_listing_action_batch(
        repository,
        ShadowBotFileQueueRunner(tmp_path / "queue-first"),
        proposal=first,
        applet_uri="weixin://launchapplet/test",
        confirmation_text=first["required_confirmation"],
        confirmed_by="tester",
        execution_attempt_id="ATTEMPT-SET-ONLINE-RELEASED-0001",
    )
    now = datetime.now(UTC).isoformat()
    with repository.connect_write() as connection, connection:
        connection.execute(
            """
            UPDATE shadowbot_write_locks
            SET status = 'RELEASED', released_at = ?, updated_at = ?
            WHERE write_identity_key = ?
            """,
            (
                now,
                now,
                first["manifest"]["items"][0]["write_identity_key"],
            ),
        )
        connection.execute(
            """
            UPDATE tasks
            SET task_status = 'pending', updated_at = ?
            WHERE task_id = ?
            """,
            (now, first_task.task_id),
        )

    second_task = _insert_set_online_task(
        repository,
        task_id="TASK-SET-ONLINE-RELEASED-0002",
    )
    second = propose_listing_action_batch(
        repository,
        batch_id="BATCH-SET-ONLINE-RELEASED-0002",
        task_ids=[second_task.task_id],
        mapping_path=mapping_path,
    )
    second_request, _ = publish_listing_action_batch(
        repository,
        ShadowBotFileQueueRunner(tmp_path / "queue-second"),
        proposal=second,
        applet_uri="weixin://launchapplet/test",
        confirmation_text=second["required_confirmation"],
        confirmed_by="tester",
        execution_attempt_id="ATTEMPT-SET-ONLINE-RELEASED-0002",
    )

    with repository.connect_read() as connection:
        lock = connection.execute(
            """
            SELECT operation_id, item_execution_attempt_id, batch_id,
                   status, released_at
            FROM shadowbot_write_locks
            WHERE write_identity_key = ?
            """,
            (
                second["manifest"]["items"][0]["write_identity_key"],
            ),
        ).fetchone()
    assert dict(lock) == {
        "operation_id": second["manifest"]["items"][0]["operation_id"],
        "item_execution_attempt_id": second_request["items"][0][
            "item_execution_attempt_id"
        ],
        "batch_id": second["manifest"]["batch_id"],
        "status": "ACTIVE",
        "released_at": None,
    }


def test_approved_retry_atomically_transfers_review_blocked_lock(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _seed_waiting_snapshot(repository, tmp_path)
    task = _insert_set_online_task(
        repository,
        task_id="TASK-SET-ONLINE-CORRECTIVE-0001",
    )
    mapping_path = _mapping_file(tmp_path / "mapping-corrective.json")
    first = propose_listing_action_batch(
        repository,
        batch_id="BATCH-SET-ONLINE-CORRECTIVE-0001",
        task_ids=[task.task_id],
        mapping_path=mapping_path,
    )
    first_request, _ = publish_listing_action_batch(
        repository,
        ShadowBotFileQueueRunner(tmp_path / "queue-corrective-first"),
        proposal=first,
        applet_uri="weixin://launchapplet/test",
        confirmation_text=first["required_confirmation"],
        confirmed_by="tester",
        execution_attempt_id="ATTEMPT-SET-ONLINE-CORRECTIVE-0001",
    )
    partial_result = _write_result(
        first_request,
        request_file_sha256="a" * 64,
    )
    partial_result["items"][0].update(
        {
            "operation_result": "PARTIALLY_APPLIED",
            "listing_effect_state": "NOT_STARTED",
            "action_confirm_clicked": False,
            "action_clicked_at": None,
            "readback_observed_at": None,
            "actual_price": None,
            "actual_inventory": None,
            "error_code": "CONTROLLED_PARTIAL",
            "error_message": "detail saved but listing action not applied",
        }
    )
    partial_result["counts"] = v5_result_counts(partial_result["items"])
    partial_result.update(derive_v5_batch_semantics(partial_result["counts"]))
    partial_result["result_payload_sha256"] = compute_listing_result_hash(
        partial_result
    )
    import_listing_action_result(
        repository,
        request=first_request,
        result=partial_result,
        result_file_sha256="b" * 64,
        source_result_path="corrective-partial.result.json",
    )
    review = repository.list_review_tasks(
        status=ReviewTaskStatus.PENDING
    )[0]
    now = datetime.now(UTC).isoformat()
    with repository.connect_write() as connection, connection:
        connection.execute(
            """
            UPDATE review_tasks
            SET review_status = 'approved',
                resolution_payload_json = ?,
                resolved_by = 'tester', resolved_at = ?, updated_at = ?
            WHERE review_task_id = ?
            """,
            (
                json.dumps(
                    {
                        "decision": "retry_task",
                        "affected_task_ids": [task.task_id],
                    },
                    ensure_ascii=False,
                ),
                now,
                now,
                review.review_task_id,
            ),
        )
        connection.execute(
            """
            UPDATE tasks
            SET task_status = 'pending', updated_at = ?
            WHERE task_id = ?
            """,
            (now, task.task_id),
        )

    retry = propose_listing_action_batch(
        repository,
        batch_id="BATCH-SET-ONLINE-CORRECTIVE-0002",
        task_ids=[task.task_id],
        mapping_path=mapping_path,
    )

    assert retry["publishable"] is True
    assert retry["corrective_authorizations"] == [
        {
            "review_task_id": review.review_task_id,
            "write_identity_key": first["manifest"]["items"][0][
                "write_identity_key"
            ],
            "previous_operation_id": first["manifest"]["items"][0][
                "operation_id"
            ],
            "source_task_id": task.task_id,
            "internal_sku": "SKU-WAITING-001",
        }
    ]
    retry_request, _ = publish_listing_action_batch(
        repository,
        ShadowBotFileQueueRunner(tmp_path / "queue-corrective-retry"),
        proposal=retry,
        applet_uri="weixin://launchapplet/test",
        confirmation_text=retry["required_confirmation"],
        confirmed_by="tester",
        execution_attempt_id="ATTEMPT-SET-ONLINE-CORRECTIVE-0002",
    )

    with repository.connect_read() as connection:
        old_operation = connection.execute(
            """
            SELECT resolution_status, resolved_by,
                   superseded_by_operation_id
            FROM shadowbot_operations
            WHERE operation_id = ?
            """,
            (first["manifest"]["items"][0]["operation_id"],),
        ).fetchone()
        lock = connection.execute(
            """
            SELECT operation_id, item_execution_attempt_id, batch_id, status
            FROM shadowbot_write_locks
            WHERE write_identity_key = ?
            """,
            (retry["manifest"]["items"][0]["write_identity_key"],),
        ).fetchone()
    assert dict(old_operation) == {
        "resolution_status": "CORRECTIVE_ACTION_AUTHORIZED",
        "resolved_by": "review_task:" + review.review_task_id,
        "superseded_by_operation_id": retry["manifest"]["items"][0][
            "operation_id"
        ],
    }
    assert dict(lock) == {
        "operation_id": retry["manifest"]["items"][0]["operation_id"],
        "item_execution_attempt_id": retry_request["items"][0][
            "item_execution_attempt_id"
        ],
        "batch_id": retry["manifest"]["batch_id"],
        "status": "ACTIVE",
    }


def test_publish_and_import_verified_set_online(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _seed_waiting_snapshot(repository, tmp_path)
    _insert_set_online_task(repository)
    proposal = propose_listing_action_batch(
        repository,
        batch_id="BATCH-SET-ONLINE-0003",
        task_ids=["TASK-SET-ONLINE-0001"],
        mapping_path=_mapping_file(tmp_path / "mapping-write.json"),
    )
    runner = ShadowBotFileQueueRunner(tmp_path / "queue")
    request, _ = publish_listing_action_batch(
        repository,
        runner,
        proposal=proposal,
        applet_uri="weixin://launchapplet/test",
        confirmation_text=proposal["required_confirmation"],
        confirmed_by="tester",
        execution_attempt_id="ATTEMPT-SET-ONLINE-0003",
    )
    request_path = (
        tmp_path
        / "queue"
        / "inbox"
        / "ATTEMPT-SET-ONLINE-0003.ready.json"
    )
    request_bytes = request_path.read_bytes()
    assert json.loads(request_bytes.decode("utf-8"))["batch_id"] == request[
        "batch_id"
    ]
    shadowbot_queue_worker._v5_validate_request(request)

    request_file_sha256 = hashlib.sha256(request_bytes).hexdigest()
    result = _write_result(
        request,
        request_file_sha256=request_file_sha256,
    )
    result_path = (
        tmp_path
        / "queue"
        / "results"
        / "ATTEMPT-SET-ONLINE-0003.result.json"
    )
    result_bytes = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    result_path.write_bytes(result_bytes)
    result_path.with_suffix(result_path.suffix + ".sha256").write_text(
        hashlib.sha256(result_bytes).hexdigest() + "\n",
        encoding="ascii",
    )
    event = ShadowBotResultImporter(
        repository,
        runner,
        tmp_path / "queue",
    ).import_one(result_path)

    assert event["summary"]["status"] == "VERIFIED"
    assert Path(event["report_path"]).read_text(encoding="utf-8").startswith(
        "# 平台商品上下架执行报告"
    )
    status = next(
        item
        for item in repository.list_listing_statuses(platform_name=PLATFORM)
        if item.internal_sku == "SKU-WAITING-001"
    )
    assert status.online_status == "online"
    assert status.current_price == Decimal("22.00")
    assert status.platform_stock_qty == 8
    assert status.online_status_source_type == "SET_ONLINE_POSTCHECK"
    task = repository.get_task("TASK-SET-ONLINE-0001")
    assert task is not None and task.task_status is TaskStatus.SUCCESS


def test_verified_set_offline_keeps_precheck_price_and_inventory_current(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _seed_waiting_snapshot(repository, tmp_path)
    action_clicked_at = "2026-07-26T06:58:47+00:00"
    readback_observed_at = "2026-07-26T06:59:13+00:00"

    with repository.connect_write() as connection, connection:
        _project_verified_listing(
            connection,
            request={
                "action_type": "set_offline",
                "platform_name": PLATFORM,
            },
            request_item={
                "internal_sku": "SKU-ONLINE-001",
                "item_execution_attempt_id": "ATTEMPT-SET-OFFLINE-0001",
                "operation_id": "OP-SET-OFFLINE-0001",
            },
            output={
                "observed_price_before_action": "21.00",
                "observed_inventory_before_action": 9,
                "action_clicked_at": action_clicked_at,
                "readback_observed_at": readback_observed_at,
            },
            now="2026-07-26T06:59:16+00:00",
        )

    status = next(
        item
        for item in repository.list_listing_statuses(platform_name=PLATFORM)
        if item.internal_sku == "SKU-ONLINE-001"
    )
    assert status.online_status == "offline"
    assert status.current_price == Decimal("21.00")
    assert status.platform_stock_qty == 9
    assert status.price_source == "SET_OFFLINE_PRECHECK"
    assert status.inventory_source == "SET_OFFLINE_PRECHECK"
    assert status.price_source_attempt_id == "ATTEMPT-SET-OFFLINE-0001"
    assert status.inventory_source_attempt_id == "ATTEMPT-SET-OFFLINE-0001"
    assert status.price_observed_at == datetime.fromisoformat(action_clicked_at)
    assert status.inventory_observed_at == datetime.fromisoformat(
        action_clicked_at
    )
    assert status.last_listing_change_at == datetime.fromisoformat(
        action_clicked_at
    )

    observations = _load_latest_platform_observations(
        repository.db_path,
        PLATFORM,
    )
    assert observations is not None
    assert observations[
        listing_identity_key(PLATFORM, "艾莎", "B级")
    ] == (Decimal("21.00"), 9)


def test_multi_offline_unknown_import_preserves_item_specific_states(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    sync_request = _request(
        repository,
        tmp_path,
        batch_id="BATCH-SYNC-OFFLINE-UNKNOWN-01",
        attempt_id="ATTEMPT-SYNC-OFFLINE-UNKNOWN-01",
    )
    sync_result = _result(
        sync_request,
        locations={
            "SKU-ONLINE-001": "online_only",
            "SKU-WAITING-001": "online_only",
            "SKU-BOTH-00001": "online_only",
        },
        scan_started_at=datetime.now(UTC).isoformat(),
    )
    sync_result["snapshot"]["scan_completed_at"] = datetime.now(UTC).isoformat()
    sync_result["snapshot"]["online_scan_started_at"] = sync_result[
        "snapshot"
    ]["scan_started_at"]
    sync_result["snapshot"]["online_scan_completed_at"] = sync_result[
        "snapshot"
    ]["scan_completed_at"]
    sync_result["snapshot"]["waiting_scan_started_at"] = sync_result[
        "snapshot"
    ]["scan_completed_at"]
    sync_result["snapshot"]["waiting_scan_completed_at"] = sync_result[
        "snapshot"
    ]["scan_completed_at"]
    sync_result["ended_at"] = sync_result["snapshot"]["scan_completed_at"]
    sync_result["result_payload_sha256"] = compute_listing_result_hash(
        sync_result
    )
    import_listing_sync_result(
        repository,
        request=sync_request,
        result=sync_result,
        result_file_sha256="a" * 64,
        source_result_path="offline-unknown-sync.result.json",
    )
    task_ids = []
    for index, sku in enumerate(
        ("SKU-ONLINE-001", "SKU-WAITING-001", "SKU-BOTH-00001"),
        start=1,
    ):
        task = _insert_set_offline_task(
            repository,
            task_id=f"TASK-SET-OFFLINE-UNKNOWN-000{index}",
            internal_sku=sku,
        )
        task_ids.append(task.task_id)
    proposal = propose_listing_action_batch(
        repository,
        batch_id="BATCH-SET-OFFLINE-UNKNOWN-0001",
        task_ids=task_ids,
        mapping_path=_mapping_file(tmp_path / "mapping-offline-unknown.json"),
    )
    queue_root = tmp_path / "queue-offline-unknown"
    request, _ = publish_listing_action_batch(
        repository,
        ShadowBotFileQueueRunner(queue_root),
        proposal=proposal,
        applet_uri="weixin://launchapplet/test",
        confirmation_text=proposal["required_confirmation"],
        confirmed_by="tester",
        execution_attempt_id="ATTEMPT-SET-OFFLINE-UNKNOWN-0001",
        fault_injection="AFTER_ACTION_CLICK_UNKNOWN",
        fault_injection_item_ordinal=2,
    )
    request_bytes = (
        queue_root
        / "inbox"
        / "ATTEMPT-SET-OFFLINE-UNKNOWN-0001.ready.json"
    ).read_bytes()
    request_sha256 = hashlib.sha256(request_bytes).hexdigest()
    now = datetime.now(UTC).isoformat()
    outcomes = (
        ("VERIFIED", "VERIFIED", True),
        ("NEEDS_RECONCILIATION", "UNKNOWN", True),
        ("NOT_ATTEMPTED", "NOT_STARTED", False),
    )
    result_items = []
    for request_item, (
        outcome,
        listing_effect,
        action_clicked,
    ) in zip(request["items"], outcomes, strict=True):
        result_items.append(
            {
                name: request_item[name]
                for name in (
                    "source_task_id",
                    "operation_id",
                    "item_execution_attempt_id",
                    "internal_sku",
                    "item_payload_sha256",
                )
            }
            | {
                "operation_result": outcome,
                "detail_effect_state": (
                    "NOT_APPLIED" if action_clicked else "NOT_STARTED"
                ),
                "listing_effect_state": listing_effect,
                "detail_save_clicked": False,
                "action_confirm_clicked": action_clicked,
                "observed_price_before_action": (
                    "21.00" if action_clicked else None
                ),
                "observed_inventory_before_action": (
                    9 if action_clicked else None
                ),
                "actual_price": "21.00" if outcome == "VERIFIED" else None,
                "actual_inventory": 9 if outcome == "VERIFIED" else None,
                "detail_save_clicked_at": None,
                "action_clicked_at": now if action_clicked else None,
                "readback_observed_at": (
                    now if outcome == "VERIFIED" else None
                ),
                "error_code": (
                    "CONTROLLED_AFTER_ACTION_CLICK_UNKNOWN"
                    if outcome == "NEEDS_RECONCILIATION"
                    else ""
                ),
                "error_message": "",
            }
        )
    counts = v5_result_counts(result_items)
    result = {
        "schema_version": "shadowbot-listing-action-batch-result-1.0",
        "contract_version": 5,
        "action_type": "set_offline",
        "batch_id": request["batch_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "execution_mode": "COMMIT",
        "manifest_sha256": request["manifest_sha256"],
        "instruction_hash": request["instruction_hash"],
        "request_file_sha256": "sha256:" + request_sha256,
        "result_id": "RESULT-SET-OFFLINE-UNKNOWN-0001",
        "started_at": now,
        "ended_at": now,
        "items": result_items,
        "counts": counts,
        **derive_v5_batch_semantics(counts),
        "error_code": "CONTROLLED_AFTER_ACTION_CLICK_UNKNOWN",
        "error_message": "受控故障",
        "retryable": False,
    }
    result["result_payload_sha256"] = compute_listing_result_hash(result)

    summary = import_listing_action_result(
        repository,
        request=request,
        result=result,
        result_file_sha256="b" * 64,
        source_result_path="offline-unknown.result.json",
    )

    assert summary["status"] == "UNKNOWN"
    assert [
        repository.get_task(task_id).task_status for task_id in task_ids
    ] == [
        TaskStatus.SUCCESS,
        TaskStatus.MANUAL_REVIEW,
        TaskStatus.PENDING,
    ]
    with repository.connect_read() as connection:
        attempts = connection.execute(
            """
            SELECT status, side_effect_state
            FROM shadowbot_execution_attempts
            WHERE execution_attempt_id IN (?, ?, ?)
            ORDER BY execution_attempt_id
            """,
            tuple(
                sorted(
                    item["item_execution_attempt_id"]
                    for item in request["items"]
                )
            ),
        ).fetchall()
        locks = connection.execute(
            """
            SELECT status
            FROM shadowbot_write_locks
            WHERE batch_id = ?
            ORDER BY write_identity_key
            """,
            (request["batch_id"],),
        ).fetchall()
    assert sorted(
        (row["status"], row["side_effect_state"]) for row in attempts
    ) == [
        ("NOT_ATTEMPTED", "NOT_STARTED"),
        ("UNKNOWN", "UNKNOWN"),
        ("VERIFIED", "VERIFIED"),
    ]
    assert sorted(row["status"] for row in locks) == [
        "RELEASED",
        "RELEASED",
        "UNKNOWN",
    ]

    unknown_item = request["items"][1]
    runner = ShadowBotFileQueueRunner(queue_root)
    runner.archive_attempt_artifacts(request["execution_attempt_id"])
    reconcile = ensure_listing_action_reconcile_attempt(
        repository,
        runner,
        source_request=request,
        source_result=result,
        operation_id=unknown_item["operation_id"],
    )
    assert reconcile["status"] == "PUBLISHED"
    repeated = ensure_listing_action_reconcile_attempt(
        repository,
        runner,
        source_request=request,
        source_result=result,
        operation_id=unknown_item["operation_id"],
    )
    assert repeated["status"] == "ALREADY_EXISTS"
    reconcile_path = Path(reconcile["queue_request_path"])
    watchdog_events = ShadowBotQueueWatchdog(
        queue_root,
        repository=repository,
    ).inspect()
    assert not any(
        event.get("error_code") == "ORPHAN_READY_REQUEST"
        for event in watchdog_events
    )
    assert reconcile_path.exists()
    reconcile_bytes = reconcile_path.read_bytes()
    reconcile_request = json.loads(reconcile_bytes.decode("utf-8-sig"))
    assert reconcile_request["execution_mode"] == "RECONCILE"
    assert reconcile_request["operation_id"] == unknown_item["operation_id"]
    assert len(reconcile_request["items"]) == 1
    assert "gate_summary" not in reconcile_request
    assert "development_confirmation" not in reconcile_request

    reconcile_item = reconcile_request["items"][0]
    reconcile_output = {
        name: reconcile_item[name]
        for name in (
            "source_task_id",
            "operation_id",
            "item_execution_attempt_id",
            "internal_sku",
            "item_payload_sha256",
        )
    } | {
        "operation_result": "VERIFIED",
        "detail_effect_state": "NOT_APPLIED",
        "listing_effect_state": "VERIFIED",
        "detail_save_clicked": False,
        "action_confirm_clicked": False,
        "observed_price_before_action": None,
        "observed_inventory_before_action": None,
        "observed_price_after_detail_save": None,
        "observed_inventory_after_detail_save": None,
        "actual_price": None,
        "actual_inventory": None,
        "detail_save_clicked_at": None,
        "action_clicked_at": None,
        "readback_observed_at": now,
        "error_code": "",
        "error_message": "",
    }
    reconcile_counts = v5_result_counts([reconcile_output])
    reconcile_result = {
        "schema_version": "shadowbot-listing-action-batch-result-1.0",
        "contract_version": 5,
        "action_type": "set_offline",
        "batch_id": reconcile_request["batch_id"],
        "execution_attempt_id": reconcile_request[
            "execution_attempt_id"
        ],
        "execution_mode": "RECONCILE",
        "manifest_sha256": reconcile_request["manifest_sha256"],
        "instruction_hash": reconcile_request["instruction_hash"],
        "request_file_sha256": "sha256:"
        + hashlib.sha256(reconcile_bytes).hexdigest(),
        "result_id": "RESULT-SET-OFFLINE-RECONCILE-0001",
        "started_at": now,
        "ended_at": now,
        "items": [reconcile_output],
        "counts": reconcile_counts,
        **derive_v5_batch_semantics(reconcile_counts),
        "error_code": "",
        "error_message": "",
        "retryable": False,
    }
    reconcile_result["result_payload_sha256"] = (
        compute_listing_result_hash(reconcile_result)
    )
    reconciled = import_listing_action_result(
        repository,
        request=reconcile_request,
        result=reconcile_result,
        result_file_sha256="c" * 64,
        source_result_path="offline-reconcile.result.json",
    )
    assert reconciled["status"] == "VERIFIED"
    assert reconciled["batch_status"] == "PARTIAL"
    assert repository.get_task(
        unknown_item["source_task_id"]
    ).task_status == TaskStatus.SUCCESS
    with repository.connect_read() as connection:
        lock = connection.execute(
            """
            SELECT status FROM shadowbot_write_locks
            WHERE operation_id = ?
            """,
            (unknown_item["operation_id"],),
        ).fetchone()
        batch = connection.execute(
            """
            SELECT status, verified_count, unknown_count,
                   not_attempted_count
            FROM shadowbot_listing_action_batches
            WHERE batch_id = ?
            """,
            (request["batch_id"],),
        ).fetchone()
        open_reviews = connection.execute(
            """
            SELECT COUNT(*) AS count FROM review_tasks
            WHERE source_task_id = ? AND review_status = 'pending'
            """,
            (unknown_item["source_task_id"],),
        ).fetchone()
    assert lock["status"] == "RELEASED"
    assert dict(batch) == {
        "status": "PARTIAL",
        "verified_count": 2,
        "unknown_count": 0,
        "not_attempted_count": 1,
    }
    assert open_reviews["count"] == 0


def test_partial_set_online_projects_complete_post_failure_snapshot(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _seed_waiting_snapshot(repository, tmp_path)
    _insert_set_online_task(repository)
    proposal = propose_listing_action_batch(
        repository,
        batch_id="BATCH-SET-ONLINE-POSTFAIL-0001",
        task_ids=["TASK-SET-ONLINE-0001"],
        mapping_path=_mapping_file(tmp_path / "mapping-postfail.json"),
    )
    request, _ = publish_listing_action_batch(
        repository,
        ShadowBotFileQueueRunner(tmp_path / "queue-postfail"),
        proposal=proposal,
        applet_uri="weixin://launchapplet/test",
        confirmation_text=proposal["required_confirmation"],
        confirmed_by="tester",
        execution_attempt_id="ATTEMPT-SET-ONLINE-POSTFAIL-0001",
    )
    result = _write_result(
        request,
        request_file_sha256="b" * 64,
    )
    output = result["items"][0]
    output.update(
        {
            "operation_result": "PARTIALLY_APPLIED",
            "listing_effect_state": "NOT_STARTED",
            "action_confirm_clicked": False,
            "action_clicked_at": None,
            "readback_observed_at": None,
            "actual_price": None,
            "actual_inventory": None,
            "error_code": "ELEMENT_NOT_FOUND",
            "error_message": "上架确认弹窗未出现",
        }
    )
    scan_started = datetime.now(UTC) + timedelta(seconds=1)
    online_completed = scan_started + timedelta(seconds=1)
    waiting_completed = online_completed + timedelta(seconds=1)
    snapshot_id = "SNAPSHOT-POSTFAIL-0001"
    snapshot_item = _item(
        snapshot_id=snapshot_id,
        suffix="0001",
        sku="SKU-WAITING-001",
        name="艾莎",
        grade="C级",
        location="waiting_only",
    )
    snapshot_item.update(
        {
            "waiting_observed_price": "22.00",
            "waiting_observed_inventory": 8,
            "waiting_observed_at": waiting_completed.isoformat(),
        }
    )
    result["post_failure_snapshot"] = {
        "schema_version": "shadowbot-listing-sync-snapshot-1.0",
        "snapshot_id": snapshot_id,
        "platform_name": PLATFORM,
        "execution_attempt_id": request["execution_attempt_id"],
        "mapping_source_version": request["mapping_source_version"],
        "result_id": result["result_id"],
        "scan_started_at": scan_started.isoformat(),
        "scan_completed_at": waiting_completed.isoformat(),
        "online_scan_started_at": scan_started.isoformat(),
        "online_scan_completed_at": online_completed.isoformat(),
        "waiting_scan_started_at": online_completed.isoformat(),
        "waiting_scan_completed_at": waiting_completed.isoformat(),
        "online_scan_complete": True,
        "waiting_scan_complete": True,
        "online_end_marker_verified": True,
        "waiting_end_marker_verified": True,
        "snapshot_complete": True,
        "instruction_hash": request["instruction_hash"],
        "status": "VERIFIED",
        "error_code": "",
        "evidence_manifest_sha256": "sha256:" + "a" * 64,
        "items": [snapshot_item],
    }
    result["ended_at"] = waiting_completed.isoformat()
    result["counts"] = v5_result_counts(result["items"])
    result.update(derive_v5_batch_semantics(result["counts"]))
    result["result_payload_sha256"] = compute_listing_result_hash(result)

    summary = import_listing_action_result(
        repository,
        request=request,
        result=result,
        result_file_sha256="c" * 64,
        source_result_path="postfail.result.json",
    )

    assert summary["status"] == "PARTIAL"
    assert summary["post_failure_snapshot_complete"] is True
    assert summary["post_failure_snapshot_id"] == snapshot_id
    assert summary["projected_skus"] == ["SKU-WAITING-001"]
    status = next(
        item
        for item in repository.list_listing_statuses(platform_name=PLATFORM)
        if item.internal_sku == "SKU-WAITING-001"
    )
    assert status.current_price == Decimal("22.00")
    assert status.platform_stock_qty == 8
    assert status.online_status == "offline"
    assert status.price_source == "shadowbot_commit_recovery_scan"
    assert status.online_status_source_type == "COMMIT_POST_FAILURE_SCAN"
    task = repository.get_task("TASK-SET-ONLINE-0001")
    assert task is not None and task.task_status is TaskStatus.MANUAL_REVIEW
    reviews = repository.list_review_tasks(status=ReviewTaskStatus.PENDING)
    assert len(reviews) == 1
    assert reviews[0].source_task_id == task.task_id
    assert reviews[0].review_type == TaskActionType.MANUAL_REVIEW.value
    assert reviews[0].required_by == reviews[0].created_at + timedelta(minutes=30)
    outbox = repository.list_notification_outbox(
        related_review_task_id=reviews[0].review_task_id
    )
    assert len(outbox) == 1
    assert outbox[0].related_task_id == task.task_id
    assert "SKU-WAITING-001" in outbox[0].payload["message"]
    assert PLATFORM in outbox[0].payload["message"]
    with repository.connect_read() as connection:
        lock = connection.execute(
            """
            SELECT status FROM shadowbot_write_locks
            WHERE write_identity_key = ?
            """,
            (request["items"][0]["write_identity_key"],),
        ).fetchone()
        stored_snapshot = connection.execute(
            """
            SELECT snapshot_complete FROM listing_sync_snapshots
            WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()
    assert lock["status"] == "REVIEW_BLOCKED"
    assert stored_snapshot["snapshot_complete"] == 1

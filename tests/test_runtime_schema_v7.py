from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models import ShadowBotBatch, ShadowBotBatchItem, ShadowBotOperationLedger
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION
from app.services.shadowbot_price_batch import (
    BatchItemStatus,
    BatchStatus,
    PriceBatchContractError,
    WRITE_LOCK_STATES,
)


NOW = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)


def _seed_task_and_review(repository: SQLiteRuntimeRepository, suffix: str) -> tuple[str, str]:
    task_id = f"TASK-{suffix}"
    review_id = f"REVIEW-{suffix}"
    now = NOW.isoformat()
    with repository.connect_write() as connection, connection:
        connection.execute(
            """
            INSERT INTO tasks(
                task_id, scope_type, scope_key, action_type, priority, task_status,
                created_at, decision_trace_json, result_message, updated_at
            ) VALUES (?, 'product', ?, 'update_price', 100, 'pending', ?, '{}', '', ?)
            """,
            (task_id, suffix, now, now),
        )
        connection.execute(
            """
            INSERT INTO review_tasks(
                review_task_id, scope_type, scope_key, source_task_id, review_type,
                review_status, review_payload_json, resolution_payload_json,
                created_at, updated_at
            ) VALUES (?, 'product', ?, ?, 'price_update', 'approved', '{}', '{}', ?, ?)
            """,
            (review_id, suffix, task_id, now, now),
        )
    return task_id, review_id


def _operation(
    task_id: str,
    suffix: str,
    *,
    page_key: str = "sha256:" + "1" * 64,
    write_key: str = "sha256:" + "2" * 64,
) -> ShadowBotOperationLedger:
    return ShadowBotOperationLedger(
        operation_id=f"OP-{suffix}",
        task_id=task_id,
        platform="ant_flower_wechat",
        product_identity={"name": "艾莎", "grade": "B级"},
        expected_old_price=Decimal("8.60"),
        target_price=Decimal("8.80"),
        status="PENDING",
        approved_payload_hash="sha256:" + "3" * 64,
        write_identity_key=write_key,
        page_identity_key=page_key,
        created_at=NOW,
        updated_at=NOW,
    )


def _batch_and_item(task_id: str, review_id: str, operation: ShadowBotOperationLedger):
    batch = ShadowBotBatch(
        batch_id="PRICE-BATCH-V7-001",
        contract_version=3,
        platform="ant_flower_wechat",
        batch_type="SERIAL_PRICE_UPDATE",
        execution_mode="FILL_PREVIEW",
        identity_normalization_version="task11-v1",
        normalized_request_digest="sha256:" + "4" * 64,
        stop_policy="PAUSE_ON_UNCERTAIN",
        source_read_batch_id="READ-BATCH-V7-001",
        source_snapshot_sha256="sha256:" + "5" * 64,
        source_page_context_sha256="sha256:" + "6" * 64,
        source_observed_at=NOW,
        source_snapshot_max_age_seconds=300,
        status=BatchStatus.PENDING.value,
        created_by="tester",
        pending_count=1,
        created_at=NOW,
        updated_at=NOW,
    )
    item = ShadowBotBatchItem(
        batch_id=batch.batch_id,
        item_id="ITEM-001",
        ordinal=1,
        source_item_id="READ-ITEM-001",
        source_read_batch_id=batch.source_read_batch_id,
        source_snapshot_sha256=batch.source_snapshot_sha256,
        source_page_context_sha256=batch.source_page_context_sha256,
        task_id=task_id,
        review_task_id=review_id,
        operation_id=operation.operation_id,
        approved_payload_hash=operation.approved_payload_hash,
        page_identity_key=operation.page_identity_key,
        write_identity_key=operation.write_identity_key,
        expected_product_name="艾莎",
        expected_grade="B级",
        approved_expected_old_price=Decimal("8.60"),
        target_price=Decimal("8.80"),
        status=BatchItemStatus.PENDING.value,
        updated_at=NOW,
    )
    return batch, item


def test_new_database_has_exact_v7_shape_and_active_identity_indexes(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    assert repository.schema_versions() == list(range(1, LATEST_RUNTIME_SCHEMA_VERSION + 1))
    health = repository.check_schema_health()
    assert health.ok, health.summary
    with repository.connect_read() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {"shadowbot_batches", "shadowbot_batch_items"}.issubset(tables)
        item_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(shadowbot_batch_items)").fetchall()
        }
        assert {"reconcile_attempt_id", "reconciliation_outcome", "reconciled_at"}.issubset(item_columns)
        for index_name in (
            "ux_shadowbot_operations_active_write_identity",
            "ux_shadowbot_operations_active_page_identity",
        ):
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                (index_name,),
            ).fetchone()[0]
            assert all(f"'{status}'" in sql for status in WRITE_LOCK_STATES)


def test_v6_physical_shape_upgrades_idempotently_to_v7(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    repository = SQLiteRuntimeRepository(path)
    repository.init_schema()
    with repository.connect_write() as connection, connection:
        for index_name in (
            "ux_shadowbot_operations_active_write_identity",
            "ux_shadowbot_operations_active_page_identity",
            "ix_shadowbot_operations_write_identity_status",
            "ix_shadowbot_operations_page_identity_status",
        ):
            connection.execute(f"DROP INDEX {index_name}")
        connection.execute("DROP TABLE shadowbot_batch_items")
        connection.execute("DROP TABLE shadowbot_batches")
        connection.execute("ALTER TABLE shadowbot_operations DROP COLUMN write_identity_key")
        connection.execute("ALTER TABLE shadowbot_operations DROP COLUMN page_identity_key")
        connection.execute("DELETE FROM runtime_schema_migrations WHERE schema_version = 7")
    assert repository.schema_versions() == [1, 2, 3, 4, 5, 6]
    assert not repository.check_schema_health().ok
    repository.init_schema()
    repository.init_schema()
    assert repository.schema_versions() == [1, 2, 3, 4, 5, 6, 7]
    assert repository.check_schema_health().ok


def test_active_write_identity_conflict_is_transactionally_rejected(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    task_1, _ = _seed_task_and_review(repository, "001")
    task_2, _ = _seed_task_and_review(repository, "002")
    first = _operation(task_1, "001")
    second = _operation(task_2, "002")
    assert repository.insert_shadowbot_operation(first) == 1
    with pytest.raises(PriceBatchContractError) as caught:
        repository.insert_shadowbot_operation(second)
    assert caught.value.code == "WRITE_LOCK_CONFLICT"
    repository.update_shadowbot_operation_status(first.operation_id, "VERIFIED")
    assert repository.insert_shadowbot_operation(second) == 1


def test_batch_and_item_persist_atomically_and_replay_by_digest(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    task_id, review_id = _seed_task_and_review(repository, "001")
    operation = _operation(task_id, "001")
    assert repository.insert_shadowbot_operation(operation) == 1
    batch, item = _batch_and_item(task_id, review_id, operation)
    assert repository.insert_shadowbot_batch(batch, [item]) == 1
    assert repository.insert_shadowbot_batch(batch, [item]) == 0
    stored_batch = repository.get_shadowbot_batch(batch.batch_id)
    stored_items = repository.list_shadowbot_batch_items(batch.batch_id)
    assert stored_batch is not None
    assert stored_batch.pending_count == 1
    assert stored_batch.normalized_request_digest == batch.normalized_request_digest
    assert len(stored_items) == 1
    assert stored_items[0].operation_id == operation.operation_id
    assert stored_items[0].target_price == Decimal("8.80")

    conflicting = replace(batch, normalized_request_digest="sha256:" + "9" * 64)
    with pytest.raises(PriceBatchContractError) as caught:
        repository.insert_shadowbot_batch(conflicting, [item])
    assert caught.value.code == "PRICE_BATCH_ID_CONFLICT"


def test_batch_insert_rejects_operation_or_approval_cross_binding(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    task_id, review_id = _seed_task_and_review(repository, "001")
    operation = _operation(task_id, "001")
    repository.insert_shadowbot_operation(operation)
    batch, item = _batch_and_item(task_id, review_id, operation)
    item.approved_payload_hash = "sha256:" + "0" * 64
    with pytest.raises(PriceBatchContractError) as caught:
        repository.insert_shadowbot_batch(batch, [item])
    assert caught.value.code == "BATCH_ITEM_BINDING_MISMATCH"
    assert repository.get_shadowbot_batch(batch.batch_id) is None


def test_schema_health_detects_incomplete_write_lock_state_predicate(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    with repository.connect_write() as connection, connection:
        connection.execute("DROP INDEX ux_shadowbot_operations_active_page_identity")
        connection.execute(
            """
            CREATE UNIQUE INDEX ux_shadowbot_operations_active_page_identity
            ON shadowbot_operations(page_identity_key)
            WHERE page_identity_key <> '' AND status IN ('PENDING', 'RUNNING')
            """
        )
    health = repository.check_schema_health()
    assert not health.ok
    assert any("complete WRITE_LOCK_STATES" in error for error in health.constraint_errors)


from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION


V12_DDL = (
    """
    CREATE TABLE runtime_schema_migrations (
        schema_version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE tasks (
        task_id TEXT PRIMARY KEY,
        trade_date TEXT,
        scope_type TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        dedupe_key TEXT NOT NULL DEFAULT '',
        internal_sku TEXT,
        platform_name TEXT,
        action_type TEXT NOT NULL,
        priority INTEGER NOT NULL,
        task_status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        scheduled_at TEXT,
        expires_at TEXT,
        expected_old_price TEXT,
        target_price TEXT,
        target_status TEXT,
        pricing_source TEXT,
        decision_trace_json TEXT NOT NULL DEFAULT '{}',
        result_message TEXT NOT NULL DEFAULT '',
        required_by TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE shadowbot_operations (
        operation_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        product_identity_json TEXT NOT NULL DEFAULT '{}',
        expected_old_price TEXT NOT NULL,
        target_price TEXT NOT NULL,
        status TEXT NOT NULL,
        lock_owner TEXT NOT NULL DEFAULT '',
        approved_payload_hash TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(task_id)
    )
    """,
    """
    CREATE TABLE shadowbot_execution_attempts (
        execution_attempt_id TEXT PRIMARY KEY,
        operation_id TEXT NOT NULL,
        execution_mode TEXT NOT NULL,
        shadowbot_run_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        side_effect_state TEXT NOT NULL,
        started_at TEXT NOT NULL,
        instruction_hash TEXT NOT NULL DEFAULT '',
        request_file_sha256 TEXT NOT NULL DEFAULT '',
        queue_request_path TEXT NOT NULL DEFAULT '',
        ended_at TEXT,
        raw_output_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(operation_id) REFERENCES shadowbot_operations(operation_id)
    )
    """,
    """
    CREATE TABLE shadowbot_side_effect_checkpoints (
        operation_id TEXT NOT NULL,
        execution_attempt_id TEXT NOT NULL,
        side_effect_state TEXT NOT NULL,
        checkpoint_at TEXT NOT NULL,
        version INTEGER NOT NULL,
        PRIMARY KEY(operation_id, version),
        FOREIGN KEY(operation_id) REFERENCES shadowbot_operations(operation_id),
        FOREIGN KEY(execution_attempt_id)
            REFERENCES shadowbot_execution_attempts(execution_attempt_id)
    )
    """,
    """
    CREATE TABLE listing_status (
        listing_status_id TEXT PRIMARY KEY,
        platform_name TEXT NOT NULL,
        internal_sku TEXT NOT NULL DEFAULT '',
        variety TEXT NOT NULL,
        grade TEXT NOT NULL,
        current_price TEXT NOT NULL,
        platform_stock_qty INTEGER NOT NULL DEFAULT 100 CHECK (
            platform_stock_qty >= 0
        ),
        sold_qty INTEGER NOT NULL DEFAULT 0 CHECK (sold_qty >= 0),
        online_status TEXT NOT NULL DEFAULT 'online' CHECK (
            online_status IN ('online', 'offline')
        ),
        source TEXT NOT NULL DEFAULT 'manual',
        updated_at TEXT NOT NULL,
        inventory_source TEXT NOT NULL DEFAULT 'default',
        inventory_observed_at TEXT,
        inventory_source_attempt_id TEXT NOT NULL DEFAULT '',
        UNIQUE(platform_name, variety, grade)
    )
    """,
    """
    CREATE TABLE shadowbot_commit_batches (
        batch_id TEXT PRIMARY KEY,
        contract_version INTEGER NOT NULL CHECK (contract_version = 4),
        execution_profile TEXT NOT NULL CHECK (
            execution_profile IN ('development', 'production')
        ),
        platform_name TEXT NOT NULL,
        manifest_sha256 TEXT NOT NULL,
        instruction_hash TEXT NOT NULL DEFAULT '',
        execution_attempt_id TEXT NOT NULL DEFAULT '',
        result_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL CHECK (status IN (
            'PREPARED', 'PUBLISHING', 'QUEUED', 'RUNNING',
            'VERIFIED', 'PARTIAL', 'FAILED', 'UNKNOWN'
        )),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE shadowbot_commit_batch_items (
        batch_id TEXT NOT NULL,
        source_task_id TEXT NOT NULL,
        internal_sku TEXT NOT NULL,
        expected_product_name TEXT NOT NULL,
        expected_grade TEXT NOT NULL,
        expected_old_price TEXT NOT NULL,
        target_price TEXT NOT NULL,
        item_payload_sha256 TEXT NOT NULL,
        preflight_row INTEGER,
        preflight_price TEXT,
        execution_ordinal INTEGER,
        submit_attempted INTEGER NOT NULL DEFAULT 0 CHECK (
            submit_attempted IN (0, 1)
        ),
        actual_price TEXT,
        status TEXT NOT NULL CHECK (status IN (
            'PENDING', 'NOT_ATTEMPTED', 'VERIFIED',
            'NOT_APPLIED', 'FAILED', 'UNKNOWN'
        )),
        error_code TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        item_id TEXT NOT NULL DEFAULT '',
        operation_id TEXT NOT NULL DEFAULT '',
        item_execution_attempt_id TEXT NOT NULL DEFAULT '',
        write_identity_key TEXT NOT NULL DEFAULT '',
        page_identity_key TEXT NOT NULL DEFAULT '',
        side_effect_state TEXT NOT NULL DEFAULT 'NOT_STARTED',
        preflight_observed_at TEXT,
        submit_intent_at TEXT,
        submit_clicked_at TEXT,
        readback_observed_at TEXT,
        PRIMARY KEY(batch_id, source_task_id),
        UNIQUE(batch_id, internal_sku),
        FOREIGN KEY(batch_id) REFERENCES shadowbot_commit_batches(batch_id),
        FOREIGN KEY(source_task_id) REFERENCES tasks(task_id)
    )
    """,
    """
    CREATE TABLE shadowbot_write_locks (
        write_identity_key TEXT PRIMARY KEY,
        operation_id TEXT NOT NULL UNIQUE,
        item_execution_attempt_id TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN ('ACTIVE', 'UNKNOWN', 'RELEASED')
        ),
        acquired_at TEXT NOT NULL,
        released_at TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(operation_id) REFERENCES shadowbot_operations(operation_id),
        FOREIGN KEY(item_execution_attempt_id)
            REFERENCES shadowbot_execution_attempts(execution_attempt_id),
        FOREIGN KEY(batch_id) REFERENCES shadowbot_commit_batches(batch_id)
    )
    """,
    """
    CREATE TABLE shadowbot_commit_result_receipts (
        result_id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        execution_attempt_id TEXT NOT NULL,
        instruction_hash TEXT NOT NULL,
        manifest_sha256 TEXT NOT NULL,
        result_sha256 TEXT NOT NULL,
        source_result_path TEXT NOT NULL DEFAULT '',
        accepted_at TEXT NOT NULL,
        ack_state TEXT NOT NULL CHECK (
            ack_state IN ('PENDING', 'WRITTEN', 'FAILED')
        ),
        ack_updated_at TEXT,
        last_projection_error TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(batch_id) REFERENCES shadowbot_commit_batches(batch_id)
    )
    """,
)


OLD_COLUMNS = {
    "shadowbot_operations": (
        "operation_id",
        "task_id",
        "platform",
        "product_identity_json",
        "expected_old_price",
        "target_price",
        "status",
        "lock_owner",
        "approved_payload_hash",
        "created_at",
        "updated_at",
    ),
    "shadowbot_execution_attempts": (
        "execution_attempt_id",
        "operation_id",
        "execution_mode",
        "shadowbot_run_id",
        "status",
        "side_effect_state",
        "started_at",
        "instruction_hash",
        "request_file_sha256",
        "queue_request_path",
        "ended_at",
        "raw_output_json",
    ),
    "shadowbot_side_effect_checkpoints": (
        "operation_id",
        "execution_attempt_id",
        "side_effect_state",
        "checkpoint_at",
        "version",
    ),
    "shadowbot_commit_batches": (
        "batch_id",
        "contract_version",
        "execution_profile",
        "platform_name",
        "manifest_sha256",
        "instruction_hash",
        "execution_attempt_id",
        "result_id",
        "status",
        "created_at",
        "updated_at",
    ),
    "shadowbot_commit_batch_items": (
        "batch_id",
        "source_task_id",
        "internal_sku",
        "expected_product_name",
        "expected_grade",
        "expected_old_price",
        "target_price",
        "item_payload_sha256",
        "item_id",
        "operation_id",
        "item_execution_attempt_id",
        "write_identity_key",
        "page_identity_key",
        "side_effect_state",
        "status",
    ),
    "shadowbot_write_locks": (
        "write_identity_key",
        "operation_id",
        "item_execution_attempt_id",
        "batch_id",
        "status",
        "acquired_at",
        "released_at",
        "updated_at",
    ),
    "shadowbot_commit_result_receipts": (
        "result_id",
        "batch_id",
        "execution_attempt_id",
        "instruction_hash",
        "manifest_sha256",
        "result_sha256",
        "ack_state",
    ),
}


def _create_populated_v12(path: Path) -> None:
    now = "2026-07-23T10:00:00+00:00"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for statement in V12_DDL:
            connection.execute(statement)
        for version in range(1, 13):
            connection.execute(
                """
                INSERT INTO runtime_schema_migrations(
                    schema_version, applied_at, note
                ) VALUES (?, ?, ?)
                """,
                (version, now, f"v{version}"),
            )
        rows = (
            (
                "TASK-V12-VERIFIED",
                "AISHA-B-60-Z",
                "success",
                "26.30",
                "26.40",
            ),
            (
                "TASK-V12-UNKNOWN",
                "CAPPUCCINO-B-60-Z",
                "manual_review",
                "46.30",
                "46.40",
            ),
        )
        for task_id, sku, status, old_price, target_price in rows:
            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, scope_type, scope_key, internal_sku,
                    platform_name, action_type, priority, task_status,
                    created_at, expected_old_price, target_price, updated_at
                ) VALUES (?, 'sku', ?, ?, '蚂蚁花团供应商',
                          'update_price', 10, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    sku,
                    sku,
                    status,
                    now,
                    old_price,
                    target_price,
                    now,
                ),
            )
        fixtures = (
            {
                "suffix": "VERIFIED",
                "task": "TASK-V12-VERIFIED",
                "sku": "AISHA-B-60-Z",
                "old": "26.30",
                "target": "26.40",
                "operation_status": "VERIFIED",
                "attempt_status": "VERIFIED",
                "effect": "VERIFIED",
                "batch_status": "VERIFIED",
                "item_status": "VERIFIED",
                "lock_status": "RELEASED",
                "result_id": "RESULT-V12-VERIFIED",
            },
            {
                "suffix": "UNKNOWN",
                "task": "TASK-V12-UNKNOWN",
                "sku": "CAPPUCCINO-B-60-Z",
                "old": "46.30",
                "target": "46.40",
                "operation_status": "NEEDS_RECONCILIATION",
                "attempt_status": "SIDE_EFFECT_UNKNOWN",
                "effect": "UNKNOWN",
                "batch_status": "UNKNOWN",
                "item_status": "UNKNOWN",
                "lock_status": "UNKNOWN",
                "result_id": "RESULT-V12-UNKNOWN",
            },
        )
        for fixture in fixtures:
            suffix = fixture["suffix"]
            operation_id = f"OP-V12-{suffix}"
            attempt_id = f"ATTEMPT-V12-{suffix}"
            batch_id = f"BATCH-V12-{suffix}"
            connection.execute(
                """
                INSERT INTO shadowbot_operations(
                    operation_id, task_id, platform, product_identity_json,
                    expected_old_price, target_price, status, lock_owner,
                    approved_payload_hash, created_at, updated_at
                ) VALUES (?, ?, '蚂蚁花团供应商', ?, ?, ?, ?, '', ?, ?, ?)
                """,
                (
                    operation_id,
                    fixture["task"],
                    json.dumps(
                        {"internal_sku": fixture["sku"]},
                        ensure_ascii=False,
                    ),
                    fixture["old"],
                    fixture["target"],
                    fixture["operation_status"],
                    f"sha256:approved-{suffix.lower()}",
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO shadowbot_execution_attempts(
                    execution_attempt_id, operation_id, execution_mode,
                    shadowbot_run_id, status, side_effect_state, started_at,
                    instruction_hash, request_file_sha256,
                    queue_request_path, ended_at, raw_output_json
                ) VALUES (?, ?, 'COMMIT', ?, ?, ?, ?, ?, ?, ?, ?, '{}')
                """,
                (
                    attempt_id,
                    operation_id,
                    f"RUN-V12-{suffix}",
                    fixture["attempt_status"],
                    fixture["effect"],
                    now,
                    f"sha256:instruction-{suffix.lower()}",
                    f"sha256:request-{suffix.lower()}",
                    f"queue/{suffix.lower()}.json",
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO shadowbot_side_effect_checkpoints(
                    operation_id, execution_attempt_id, side_effect_state,
                    checkpoint_at, version
                ) VALUES (?, ?, ?, ?, 1)
                """,
                (operation_id, attempt_id, fixture["effect"], now),
            )
            connection.execute(
                """
                INSERT INTO shadowbot_commit_batches(
                    batch_id, contract_version, execution_profile,
                    platform_name, manifest_sha256, instruction_hash,
                    execution_attempt_id, result_id, status,
                    created_at, updated_at
                ) VALUES (?, 4, 'production', '蚂蚁花团供应商',
                          ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    f"sha256:manifest-{suffix.lower()}",
                    f"sha256:instruction-{suffix.lower()}",
                    attempt_id,
                    fixture["result_id"],
                    fixture["batch_status"],
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO shadowbot_commit_batch_items(
                    batch_id, source_task_id, internal_sku,
                    expected_product_name, expected_grade,
                    expected_old_price, target_price, item_payload_sha256,
                    preflight_row, preflight_price, execution_ordinal,
                    submit_attempted, actual_price, status,
                    updated_at, item_id, operation_id,
                    item_execution_attempt_id, write_identity_key,
                    page_identity_key, side_effect_state,
                    preflight_observed_at, submit_intent_at,
                    submit_clicked_at, readback_observed_at
                ) VALUES (?, ?, ?, ?, 'B级', ?, ?, ?, 1, ?, 1, 1,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    fixture["task"],
                    fixture["sku"],
                    "艾莎" if suffix == "VERIFIED" else "卡布奇诺",
                    fixture["old"],
                    fixture["target"],
                    f"sha256:item-{suffix.lower()}",
                    fixture["old"],
                    (
                        fixture["target"]
                        if suffix == "VERIFIED"
                        else fixture["old"]
                    ),
                    fixture["item_status"],
                    now,
                    f"ITEM-V12-{suffix}",
                    operation_id,
                    attempt_id,
                    f"write:{fixture['sku']}",
                    f"page:{fixture['sku']}",
                    fixture["effect"],
                    now,
                    now,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO shadowbot_write_locks(
                    write_identity_key, operation_id,
                    item_execution_attempt_id, batch_id, status,
                    acquired_at, released_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"write:{fixture['sku']}",
                    operation_id,
                    attempt_id,
                    batch_id,
                    fixture["lock_status"],
                    now,
                    now if suffix == "VERIFIED" else None,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO shadowbot_commit_result_receipts(
                    result_id, batch_id, execution_attempt_id,
                    instruction_hash, manifest_sha256, result_sha256,
                    source_result_path, accepted_at, ack_state,
                    ack_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'WRITTEN', ?)
                """,
                (
                    fixture["result_id"],
                    batch_id,
                    attempt_id,
                    f"sha256:instruction-{suffix.lower()}",
                    f"sha256:manifest-{suffix.lower()}",
                    f"sha256:result-{suffix.lower()}",
                    f"results/{suffix.lower()}.json",
                    now,
                    now,
                ),
            )
        connection.execute(
            """
            INSERT INTO listing_status(
                listing_status_id, platform_name, internal_sku,
                variety, grade, current_price, platform_stock_qty,
                sold_qty, online_status, source, updated_at,
                inventory_source, inventory_observed_at,
                inventory_source_attempt_id
            ) VALUES (
                'LISTING-V12-AISHA-B', '蚂蚁花团供应商',
                'AISHA-B-60-Z', '艾莎', 'B级', '26.40',
                12, 0, 'online', 'shadowbot', ?, 'shadowbot', ?,
                'ATTEMPT-V12-VERIFIED'
            )
            """,
            (now, now),
        )


def _table_digest(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> tuple[int, str]:
    column_sql = ", ".join(f'"{column}"' for column in columns)
    rows = connection.execute(
        f"SELECT {column_sql} FROM {table} ORDER BY {column_sql}"
    ).fetchall()
    payload = json.dumps(
        [list(row) for row in rows],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return len(rows), hashlib.sha256(payload).hexdigest()


def _old_digests(path: Path) -> dict[str, tuple[int, str]]:
    with closing(sqlite3.connect(path)) as connection:
        return {
            table: _table_digest(connection, table, columns)
            for table, columns in OLD_COLUMNS.items()
        }


def test_v13_new_database_has_exact_schema_and_nullable_action_prices(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime.sqlite3"
    repository = SQLiteRuntimeRepository(path)
    repository.init_schema()

    assert repository.schema_versions() == list(
        range(1, LATEST_RUNTIME_SCHEMA_VERSION + 1)
    )
    health = repository.check_schema_health()
    assert health.ok, health.summary
    with closing(repository.connect_read()) as connection:
        operation_columns = {
            str(row["name"]): row
            for row in connection.execute(
                "PRAGMA table_info(shadowbot_operations)"
            )
        }
        assert int(operation_columns["expected_old_price"]["notnull"]) == 0
        assert int(operation_columns["target_price"]["notnull"]) == 0
        lock_foreign_keys = {
            (str(row["from"]), str(row["table"]), str(row["to"]))
            for row in connection.execute(
                "PRAGMA foreign_key_list(shadowbot_write_locks)"
            )
        }
        assert (
            "batch_id",
            "shadowbot_batch_registry",
            "batch_id",
        ) in lock_foreign_keys
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v12_to_v13_migration_preserves_v4_ledgers_and_unknown_lock(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime-v12.sqlite3"
    _create_populated_v12(path)
    before = _old_digests(path)

    repository = SQLiteRuntimeRepository(path)
    repository.init_schema()

    assert repository.schema_versions() == list(range(1, 14))
    health = repository.check_schema_health()
    assert health.ok, health.summary
    after = _old_digests(path)
    assert after == before
    with closing(repository.connect_read()) as connection:
        operations = connection.execute(
            """
            SELECT operation_id, action_type, expected_old_status,
                   target_status, target_inventory, approved_payload_hash
            FROM shadowbot_operations ORDER BY operation_id
            """
        ).fetchall()
        assert [str(row["action_type"]) for row in operations] == [
            "update_price",
            "update_price",
        ]
        assert all(row["expected_old_status"] is None for row in operations)
        assert all(row["target_status"] is None for row in operations)
        assert all(row["target_inventory"] is None for row in operations)
        assert {
            str(row["approved_payload_hash"]) for row in operations
        } == {
            "sha256:approved-unknown",
            "sha256:approved-verified",
        }
        registry = connection.execute(
            """
            SELECT batch_id, batch_type, contract_version, platform_name
            FROM shadowbot_batch_registry ORDER BY batch_id
            """
        ).fetchall()
        assert len(registry) == 2
        assert {str(row["batch_type"]) for row in registry} == {
            "update_price"
        }
        assert {int(row["contract_version"]) for row in registry} == {4}
        locks = connection.execute(
            "SELECT operation_id, status FROM shadowbot_write_locks "
            "ORDER BY operation_id"
        ).fetchall()
        assert [(str(row["operation_id"]), str(row["status"])) for row in locks] == [
            ("OP-V12-UNKNOWN", "UNKNOWN"),
            ("OP-V12-VERIFIED", "RELEASED"),
        ]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for table in (
            "shadowbot_listing_action_batches",
            "shadowbot_listing_action_batch_items",
            "shadowbot_listing_result_receipts",
            "listing_sync_snapshots",
            "listing_sync_snapshot_items",
            "listing_anomaly_cases",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] == 0

    repository.init_schema()
    assert repository.check_schema_health().ok
    assert _old_digests(path) == before


def test_v13_operation_checks_allow_listing_actions_without_fake_prices(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime.sqlite3"
    repository = SQLiteRuntimeRepository(path)
    repository.init_schema()
    now = "2026-07-25T12:00:00+00:00"
    with closing(repository.connect_write()) as connection, connection:
        for task_id, action in (
            ("TASK-SET-ONLINE", "set_online"),
            ("TASK-SET-OFFLINE", "set_offline"),
        ):
            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, scope_type, scope_key, action_type, priority,
                    task_status, created_at, updated_at
                ) VALUES (?, 'sku', ?, ?, 10, 'pending', ?, ?)
                """,
                (task_id, task_id, action, now, now),
            )
        connection.execute(
            """
            INSERT INTO shadowbot_operations(
                operation_id, task_id, platform, action_type,
                expected_old_price, target_price, expected_old_status,
                target_status, target_inventory, status, created_at, updated_at
            ) VALUES (
                'OP-SET-ONLINE', 'TASK-SET-ONLINE', 'platform',
                'set_online', NULL, '26.40', 'offline', 'online', 12,
                'PENDING', ?, ?
            )
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO shadowbot_operations(
                operation_id, task_id, platform, action_type,
                expected_old_price, target_price, expected_old_status,
                target_status, target_inventory, status, created_at, updated_at
            ) VALUES (
                'OP-SET-OFFLINE', 'TASK-SET-OFFLINE', 'platform',
                'set_offline', NULL, NULL, 'online', 'offline', NULL,
                'PENDING', ?, ?
            )
            """,
            (now, now),
        )
    with closing(repository.connect_read()) as connection:
        rows = connection.execute(
            """
            SELECT action_type, expected_old_price, target_price,
                   target_inventory
            FROM shadowbot_operations
            WHERE operation_id IN ('OP-SET-ONLINE', 'OP-SET-OFFLINE')
            ORDER BY action_type
            """
        ).fetchall()
    assert [str(row["action_type"]) for row in rows] == [
        "set_offline",
        "set_online",
    ]
    assert rows[0]["expected_old_price"] is None
    assert rows[0]["target_price"] is None
    assert rows[0]["target_inventory"] is None


def test_v13_health_rejects_missing_required_index(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    repository = SQLiteRuntimeRepository(path)
    repository.init_schema()
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            "DROP INDEX ix_listing_sync_snapshot_items_internal_sku"
        )

    health = repository.check_schema_health()
    assert not health.ok
    assert (
        "ix_listing_sync_snapshot_items_internal_sku"
        in health.missing_indexes
    )


def test_v12_to_v13_migration_rolls_back_on_orphaned_lock(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime-v12-invalid.sqlite3"
    _create_populated_v12(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            UPDATE shadowbot_write_locks
            SET batch_id = 'BATCH-V12-MISSING'
            WHERE operation_id = 'OP-V12-UNKNOWN'
            """
        )

    repository = SQLiteRuntimeRepository(path)
    try:
        repository.init_schema()
    except RuntimeError as exc:
        assert "foreign key violations" in str(exc)
    else:
        raise AssertionError("orphaned v12 lock must abort the v13 migration")

    with closing(sqlite3.connect(path)) as connection:
        versions = [
            int(row[0])
            for row in connection.execute(
                "SELECT schema_version FROM runtime_schema_migrations "
                "ORDER BY schema_version"
            )
        ]
        operation_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(shadowbot_operations)"
            )
        }
        registry = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'shadowbot_batch_registry'"
        ).fetchone()
    assert versions == list(range(1, 13))
    assert "action_type" not in operation_columns
    assert registry is None

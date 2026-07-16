from __future__ import annotations

import sqlite3
import tempfile
import unittest
import gc
from datetime import datetime
from pathlib import Path

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION


# These fixtures intentionally live outside app.repositories.sqlite_runtime_repository.
# They are fixed snapshots of the historical runtime shapes rather than a filtered
# view of the current SCHEMA_SQL.  That keeps migration tests useful when the
# current schema changes.
HISTORICAL_BASE_DDL = (
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
    CREATE UNIQUE INDEX ux_tasks_open_dedupe
    ON tasks(dedupe_key)
    WHERE dedupe_key <> ''
      AND task_status NOT IN ('success', 'skipped', 'cancelled', 'expired')
    """,
    """
    CREATE TABLE review_tasks (
        review_task_id TEXT PRIMARY KEY,
        trade_date TEXT,
        scope_type TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        dedupe_key TEXT NOT NULL DEFAULT '',
        source_task_id TEXT,
        review_type TEXT NOT NULL,
        review_status TEXT NOT NULL,
        internal_sku TEXT,
        platform_name TEXT,
        reason TEXT NOT NULL DEFAULT '',
        review_payload_json TEXT NOT NULL DEFAULT '{}',
        resolution_payload_json TEXT NOT NULL DEFAULT '{}',
        required_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        resolved_by TEXT NOT NULL DEFAULT '',
        resolved_at TEXT,
        resolution_note TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(source_task_id) REFERENCES tasks(task_id)
    )
    """,
    """
    CREATE UNIQUE INDEX ux_review_tasks_pending_dedupe
    ON review_tasks(dedupe_key)
    WHERE dedupe_key <> '' AND review_status = 'pending'
    """,
    """
    CREATE TABLE review_tokens (
        token_id TEXT PRIMARY KEY,
        review_task_id TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        token_subject TEXT NOT NULL,
        allowed_actions TEXT NOT NULL DEFAULT '[]',
        expires_at TEXT NOT NULL,
        used_at TEXT,
        revoked_at TEXT,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL DEFAULT 'system',
        last_used_at TEXT,
        note TEXT,
        FOREIGN KEY(review_task_id) REFERENCES review_tasks(review_task_id)
    )
    """,
    """
    CREATE INDEX ix_review_tokens_review_task_id ON review_tokens(review_task_id)
    """,
    """
    CREATE INDEX ix_review_tokens_expires_at ON review_tokens(expires_at)
    """,
    """
    CREATE TABLE execution_logs (
        log_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        executor_name TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT,
        success_flag INTEGER,
        error_code TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        raw_output TEXT NOT NULL DEFAULT '',
        ai_model_version TEXT NOT NULL DEFAULT '',
        ai_summary TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(task_id)
    )
    """,
    """
    CREATE TABLE notification_logs (
        notification_id TEXT PRIMARY KEY,
        related_task_id TEXT,
        related_review_task_id TEXT,
        recipient_type TEXT NOT NULL,
        recipient TEXT NOT NULL,
        channel TEXT NOT NULL,
        sent_at TEXT,
        send_status TEXT NOT NULL,
        dedupe_key TEXT NOT NULL DEFAULT '',
        message TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(related_task_id) REFERENCES tasks(task_id),
        FOREIGN KEY(related_review_task_id) REFERENCES review_tasks(review_task_id)
    )
    """,
    """
    CREATE UNIQUE INDEX ux_notification_logs_dedupe
    ON notification_logs(dedupe_key)
    WHERE dedupe_key <> ''
    """,
    """
    CREATE TABLE task_status_history (
        history_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT NOT NULL,
        changed_by TEXT NOT NULL,
        changed_at TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(task_id) REFERENCES tasks(task_id)
    )
    """,
)

HISTORICAL_V3_DDL = HISTORICAL_BASE_DDL + (
    """
    CREATE TABLE script_runs (
        script_run_id TEXT PRIMARY KEY,
        evaluator_id TEXT NOT NULL,
        evaluator_name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        run_mode TEXT NOT NULL,
        run_status TEXT NOT NULL,
        trade_date TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        summary_json TEXT NOT NULL DEFAULT '{}',
        error_message TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT 'system'
    )
    """,
    """
    CREATE TABLE script_run_items (
        item_id TEXT PRIMARY KEY,
        script_run_id TEXT NOT NULL,
        proposal_type TEXT NOT NULL,
        dedupe_key TEXT NOT NULL,
        severity TEXT NOT NULL,
        item_status TEXT NOT NULL,
        message TEXT NOT NULL DEFAULT '',
        payload_json TEXT NOT NULL DEFAULT '{}',
        decision_trace_json TEXT NOT NULL DEFAULT '{}',
        related_task_id TEXT,
        related_review_task_id TEXT,
        related_notification_id TEXT,
        error_message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(script_run_id) REFERENCES script_runs(script_run_id),
        FOREIGN KEY(related_task_id) REFERENCES tasks(task_id),
        FOREIGN KEY(related_review_task_id) REFERENCES review_tasks(review_task_id),
        FOREIGN KEY(related_notification_id) REFERENCES notification_logs(notification_id)
    )
    """,
    """
    CREATE INDEX ix_script_run_items_script_run_id ON script_run_items(script_run_id)
    """,
    """
    CREATE INDEX ix_script_runs_started_at ON script_runs(started_at)
    """,
)

HISTORICAL_V4_SHADOWBOT_DDL = (
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
        ended_at TEXT,
        raw_output_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(operation_id) REFERENCES shadowbot_operations(operation_id)
    )
    """,
    """
    CREATE INDEX ix_shadowbot_execution_attempts_operation_id
    ON shadowbot_execution_attempts(operation_id)
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
        FOREIGN KEY(execution_attempt_id) REFERENCES shadowbot_execution_attempts(execution_attempt_id)
    )
    """,
)

HISTORICAL_LEGACY_V5_SHADOWBOT_DDL = (
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
    CREATE INDEX ix_shadowbot_execution_attempts_operation_id
    ON shadowbot_execution_attempts(operation_id)
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
        FOREIGN KEY(execution_attempt_id) REFERENCES shadowbot_execution_attempts(execution_attempt_id)
    )
    """,
)


class RuntimeSchemaV5Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "runtime.sqlite3"

    def tearDown(self) -> None:
        gc.collect()
        self.temp_dir.cleanup()

    def test_new_database_has_exact_v5_and_retry_authorization_shape(self) -> None:
        repository = SQLiteRuntimeRepository(self.db_path)
        repository.init_schema()

        self.assertEqual(repository.schema_versions(), [1, 2, 3, 4, 5, 6])
        health = repository.check_schema_health()
        self.assertTrue(health.ok, health.summary)
        self.assertEqual(health.actual_version, LATEST_RUNTIME_SCHEMA_VERSION)

        with sqlite3.connect(self.db_path) as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(retry_authorizations)").fetchall()
            }
            indexes = {
                row[1]
                for row in connection.execute("PRAGMA index_list(retry_authorizations)").fetchall()
            }
        self.assertEqual(
            columns,
            {
                "retry_authorization_id",
                "operation_id",
                "source_execution_attempt_id",
                "authorization_type",
                "authorized_by",
                "evidence_type",
                "evidence_hash",
                "approved_payload_hash",
                "status",
                "max_uses",
                "consumed_by_execution_attempt_id",
                "expires_at",
                "reason",
                "created_at",
                "consumed_at",
            },
        )
        self.assertTrue(
            {
                "ux_retry_authorizations_evidence_hash",
                "ux_retry_authorizations_consumed_by_execution_attempt_id",
                "ix_retry_authorizations_operation_id",
                "ix_retry_authorizations_status",
                "ix_retry_authorizations_expires_at",
            }.issubset(indexes)
        )

    @staticmethod
    def _create_historical_database(path: Path, version: int) -> dict[str, str]:
        """Build a fixed v3 or v4 snapshot, never derived from current SQL."""

        if version == 3:
            statements = HISTORICAL_V3_DDL
        elif version == 4:
            statements = HISTORICAL_V3_DDL + HISTORICAL_V4_SHADOWBOT_DDL
        else:
            raise ValueError(f"unsupported historical version: {version}")

        suffix = str(version)
        ids = {
            "task": f"legacy-task-v{suffix}",
            "review": f"legacy-review-v{suffix}",
            "script": f"legacy-script-v{suffix}",
            "operation": f"legacy-operation-v{suffix}",
            "attempt": f"legacy-attempt-v{suffix}",
        }
        now = datetime(2026, 7, 14, 12, 0).isoformat()
        with sqlite3.connect(path) as connection:
            for statement in statements:
                connection.execute(statement)
            for applied_version in range(1, version + 1):
                connection.execute(
                    "INSERT INTO runtime_schema_migrations(schema_version, applied_at, note) VALUES (?, ?, ?)",
                    (applied_version, now, f"historical v{applied_version}"),
                )
            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, scope_type, scope_key, action_type, priority, task_status,
                    created_at, updated_at
                ) VALUES (?, 'global', ?, 'manual_review', 1, 'pending', ?, ?)
                """,
                (ids["task"], f"legacy-scope-v{suffix}", now, now),
            )
            connection.execute(
                """
                INSERT INTO review_tasks(
                    review_task_id, scope_type, scope_key, source_task_id,
                    review_type, review_status, created_at, updated_at
                ) VALUES (?, 'global', ?, ?, 'manual_review', 'pending', ?, ?)
                """,
                (ids["review"], f"legacy-scope-v{suffix}", ids["task"], now, now),
            )
            connection.execute(
                """
                INSERT INTO script_runs(
                    script_run_id, evaluator_id, evaluator_name, run_mode,
                    run_status, started_at
                ) VALUES (?, 'legacy-evaluator', 'Legacy evaluator', 'preview', 'success', ?)
                """,
                (ids["script"], now),
            )
            if version == 4:
                connection.execute(
                    """
                    INSERT INTO shadowbot_operations(
                        operation_id, task_id, platform, expected_old_price,
                        target_price, status, created_at, updated_at
                    ) VALUES (?, ?, 'legacy-platform', '10', '11', 'pending', ?, ?)
                    """,
                    (ids["operation"], ids["task"], now, now),
                )
                connection.execute(
                    """
                    INSERT INTO shadowbot_execution_attempts(
                        execution_attempt_id, operation_id, execution_mode, status,
                        side_effect_state, started_at
                    ) VALUES (?, ?, 'legacy', 'started', 'none', ?)
                    """,
                    (ids["attempt"], ids["operation"], now),
                )
        return ids

    @staticmethod
    def _create_legacy_v5_database(path: Path) -> dict[str, str]:
        """Build the main@275e634 shape: v1..v5, queue audit, no retry table."""

        statements = HISTORICAL_V3_DDL + HISTORICAL_LEGACY_V5_SHADOWBOT_DDL
        ids = {
            "task": "legacy-task-v5",
            "review": "legacy-review-v5",
            "script": "legacy-script-v5",
            "operation": "legacy-operation-v5",
            "attempt": "legacy-attempt-v5",
        }
        now = datetime(2026, 7, 14, 12, 0).isoformat()
        with sqlite3.connect(path) as connection:
            for statement in statements:
                connection.execute(statement)
            for applied_version in range(1, 6):
                note = "shadowbot file queue audit fields" if applied_version == 5 else f"historical v{applied_version}"
                connection.execute(
                    "INSERT INTO runtime_schema_migrations(schema_version, applied_at, note) VALUES (?, ?, ?)",
                    (applied_version, now, note),
                )
            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, scope_type, scope_key, action_type, priority, task_status,
                    created_at, updated_at
                ) VALUES (?, 'global', 'legacy-scope-v5', 'manual_review', 1, 'pending', ?, ?)
                """,
                (ids["task"], now, now),
            )
            connection.execute(
                """
                INSERT INTO review_tasks(
                    review_task_id, scope_type, scope_key, source_task_id,
                    review_type, review_status, created_at, updated_at
                ) VALUES (?, 'global', 'legacy-scope-v5', ?, 'manual_review', 'pending', ?, ?)
                """,
                (ids["review"], ids["task"], now, now),
            )
            connection.execute(
                """
                INSERT INTO script_runs(
                    script_run_id, evaluator_id, evaluator_name, run_mode,
                    run_status, started_at
                ) VALUES (?, 'legacy-evaluator', 'Legacy evaluator', 'preview', 'success', ?)
                """,
                (ids["script"], now),
            )
            connection.execute(
                """
                INSERT INTO shadowbot_operations(
                    operation_id, task_id, platform, expected_old_price,
                    target_price, status, created_at, updated_at
                ) VALUES (?, ?, 'legacy-platform', '10', '11', 'pending', ?, ?)
                """,
                (ids["operation"], ids["task"], now, now),
            )
            connection.execute(
                """
                INSERT INTO shadowbot_execution_attempts(
                    execution_attempt_id, operation_id, execution_mode, status,
                    side_effect_state, started_at, instruction_hash,
                    request_file_sha256, queue_request_path
                ) VALUES (?, ?, 'legacy', 'started', 'none', ?, 'legacy-instruction',
                          'legacy-request', 'legacy/request.json')
                """,
                (ids["attempt"], ids["operation"], now),
            )
        return ids

    def test_v3_and_v4_databases_upgrade_to_v5(self) -> None:
        for version in (3, 4):
            with self.subTest(version=version):
                path = Path(self.temp_dir.name) / f"v{version}.sqlite3"
                ids = self._create_historical_database(path, version)
                repository = SQLiteRuntimeRepository(path)
                with sqlite3.connect(path) as connection:
                    self.assertEqual(repository.schema_versions(), list(range(1, version + 1)))
                    shadowbot_tables = {
                        row[0]
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'shadowbot_%'"
                        ).fetchall()
                    }
                    if version == 3:
                        self.assertEqual(shadowbot_tables, set())
                    else:
                        self.assertIn("shadowbot_operations", shadowbot_tables)
                        self.assertIn("shadowbot_execution_attempts", shadowbot_tables)
                repository.init_schema()
                self.assertEqual(repository.schema_versions(), [1, 2, 3, 4, 5, 6])
                health = repository.check_schema_health()
                self.assertTrue(health.ok, health.summary)
                with sqlite3.connect(path) as connection:
                    self.assertEqual(
                        connection.execute("SELECT task_id FROM tasks WHERE task_id = ?", (ids["task"],)).fetchone()[0],
                        ids["task"],
                    )
                    self.assertEqual(
                        connection.execute("SELECT review_task_id FROM review_tasks WHERE review_task_id = ?", (ids["review"],)).fetchone()[0],
                        ids["review"],
                    )
                    self.assertEqual(
                        connection.execute("SELECT script_run_id FROM script_runs WHERE script_run_id = ?", (ids["script"],)).fetchone()[0],
                        ids["script"],
                    )
                    if version == 4:
                        self.assertEqual(
                            connection.execute("SELECT operation_id FROM shadowbot_operations WHERE operation_id = ?", (ids["operation"],)).fetchone()[0],
                            ids["operation"],
                        )
                        self.assertEqual(
                            connection.execute("SELECT execution_attempt_id FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?", (ids["attempt"],)).fetchone()[0],
                            ids["attempt"],
                        )
                        self.assertTrue(
                            {
                                "instruction_hash",
                                "request_file_sha256",
                                "queue_request_path",
                            }.issubset(
                                {
                                    row[1]
                                    for row in connection.execute(
                                        "PRAGMA table_info(shadowbot_execution_attempts)"
                                    ).fetchall()
                                }
                            )
                        )
                repository.init_schema()
                self.assertEqual(repository.schema_versions(), [1, 2, 3, 4, 5, 6])
                self.assertTrue(repository.check_schema_health().ok)
                with sqlite3.connect(path) as connection:
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM tasks WHERE task_id = ?", (ids["task"],)).fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM script_runs WHERE script_run_id = ?", (ids["script"],)).fetchone()[0],
                        1,
                    )

    def test_legacy_v5_database_gains_retry_authorization_and_corrects_note(self) -> None:
        path = Path(self.temp_dir.name) / "legacy-v5.sqlite3"
        ids = self._create_legacy_v5_database(path)
        repository = SQLiteRuntimeRepository(path)
        with sqlite3.connect(path) as connection:
            self.assertEqual(repository.schema_versions(), [1, 2, 3, 4, 5])
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'retry_authorizations'"
                ).fetchone()
            )
            attempt_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(shadowbot_execution_attempts)").fetchall()
            }
            self.assertTrue(
                {"instruction_hash", "request_file_sha256", "queue_request_path"}.issubset(attempt_columns)
            )
            self.assertEqual(
                connection.execute(
                    "SELECT note FROM runtime_schema_migrations WHERE schema_version = 5"
                ).fetchone()[0],
                "shadowbot file queue audit fields",
            )

        repository.init_schema()
        self.assertEqual(repository.schema_versions(), [1, 2, 3, 4, 5, 6])
        health = repository.check_schema_health()
        self.assertTrue(health.ok, health.summary)
        with sqlite3.connect(path) as connection:
            self.assertIsNotNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'retry_authorizations'"
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT note FROM runtime_schema_migrations WHERE schema_version = 5"
                ).fetchone()[0],
                "retry authorization persistence and shadowbot file queue audit fields",
            )
            self.assertEqual(
                connection.execute("SELECT task_id FROM tasks WHERE task_id = ?", (ids["task"],)).fetchone()[0],
                ids["task"],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT queue_request_path FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?",
                    (ids["attempt"],),
                ).fetchone()[0],
                "legacy/request.json",
            )

        repository.init_schema()
        self.assertTrue(repository.check_schema_health().ok)
        with sqlite3.connect(path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM shadowbot_operations WHERE operation_id = ?", (ids["operation"],)).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?", (ids["attempt"],)).fetchone()[0],
                1,
            )

    def test_health_rejects_indexes_on_wrong_table_or_columns(self) -> None:
        for mutation in ("wrong_table", "wrong_columns"):
            with self.subTest(mutation=mutation):
                path = Path(self.temp_dir.name) / f"{mutation}.sqlite3"
                repository = SQLiteRuntimeRepository(path)
                repository.init_schema()
                with sqlite3.connect(path) as connection:
                    connection.execute("DROP INDEX ix_retry_authorizations_operation_id")
                    target = "tasks(task_id)" if mutation == "wrong_table" else "retry_authorizations(status)"
                    connection.execute(
                        f"CREATE INDEX ix_retry_authorizations_operation_id ON {target}"
                    )
                health = repository.check_schema_health()
                self.assertFalse(health.ok)
                self.assertTrue(
                    any("ix_retry_authorizations_operation_id" in error for error in health.constraint_errors)
                    or "ix_retry_authorizations_operation_id" in health.missing_indexes
                )

    def test_health_rejects_retry_authorization_foreign_keys_with_wrong_target_columns(self) -> None:
        repository = SQLiteRuntimeRepository(self.db_path)
        repository.init_schema()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("ALTER TABLE retry_authorizations RENAME TO retry_authorizations_legacy")
            for index_name in (
                "ux_retry_authorizations_evidence_hash",
                "ux_retry_authorizations_consumed_by_execution_attempt_id",
                "ix_retry_authorizations_operation_id",
                "ix_retry_authorizations_status",
                "ix_retry_authorizations_expires_at",
            ):
                connection.execute(f"DROP INDEX {index_name}")
            connection.execute(
                """
                CREATE TABLE retry_authorizations (
                    retry_authorization_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    source_execution_attempt_id TEXT NOT NULL,
                    authorization_type TEXT NOT NULL,
                    authorized_by TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    approved_payload_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'CONSUMED', 'EXPIRED', 'REVOKED')),
                    max_uses INTEGER NOT NULL DEFAULT 1 CHECK (max_uses = 1),
                    consumed_by_execution_attempt_id TEXT,
                    expires_at TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(operation_id) REFERENCES shadowbot_operations(task_id),
                    FOREIGN KEY(source_execution_attempt_id) REFERENCES shadowbot_execution_attempts(operation_id)
                )
                """
            )
            connection.execute("DROP TABLE retry_authorizations_legacy")
            connection.execute(
                "CREATE UNIQUE INDEX ux_retry_authorizations_evidence_hash ON retry_authorizations(evidence_hash)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX ux_retry_authorizations_consumed_by_execution_attempt_id "
                "ON retry_authorizations(consumed_by_execution_attempt_id)"
            )
            connection.execute(
                "CREATE INDEX ix_retry_authorizations_operation_id ON retry_authorizations(operation_id)"
            )
            connection.execute("CREATE INDEX ix_retry_authorizations_status ON retry_authorizations(status)")
            connection.execute("CREATE INDEX ix_retry_authorizations_expires_at ON retry_authorizations(expires_at)")
        health = repository.check_schema_health()
        self.assertFalse(health.ok)
        self.assertIn(
            "missing foreign key operation_id -> shadowbot_operations(operation_id)",
            health.constraint_errors,
        )
        self.assertIn(
            "missing foreign key source_execution_attempt_id -> shadowbot_execution_attempts(execution_attempt_id)",
            health.constraint_errors,
        )

    def test_health_rejects_retry_authorization_status_check_with_extra_value(self) -> None:
        repository = SQLiteRuntimeRepository(self.db_path)
        repository.init_schema()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("ALTER TABLE retry_authorizations RENAME TO retry_authorizations_legacy")
            for index_name in (
                "ux_retry_authorizations_evidence_hash",
                "ux_retry_authorizations_consumed_by_execution_attempt_id",
                "ix_retry_authorizations_operation_id",
                "ix_retry_authorizations_status",
                "ix_retry_authorizations_expires_at",
            ):
                connection.execute(f"DROP INDEX {index_name}")
            connection.execute(
                """
                CREATE TABLE retry_authorizations (
                    retry_authorization_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    source_execution_attempt_id TEXT NOT NULL,
                    authorization_type TEXT NOT NULL,
                    authorized_by TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    approved_payload_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'CONSUMED', 'EXPIRED', 'REVOKED', 'UNKNOWN')),
                    max_uses INTEGER NOT NULL DEFAULT 1 CHECK (max_uses = 1),
                    consumed_by_execution_attempt_id TEXT,
                    expires_at TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(operation_id) REFERENCES shadowbot_operations(operation_id),
                    FOREIGN KEY(source_execution_attempt_id) REFERENCES shadowbot_execution_attempts(execution_attempt_id)
                )
                """
            )
            connection.execute("DROP TABLE retry_authorizations_legacy")
            connection.execute(
                "CREATE UNIQUE INDEX ux_retry_authorizations_evidence_hash ON retry_authorizations(evidence_hash)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX ux_retry_authorizations_consumed_by_execution_attempt_id ON retry_authorizations(consumed_by_execution_attempt_id)"
            )
            connection.execute(
                "CREATE INDEX ix_retry_authorizations_operation_id ON retry_authorizations(operation_id)"
            )
            connection.execute("CREATE INDEX ix_retry_authorizations_status ON retry_authorizations(status)")
            connection.execute("CREATE INDEX ix_retry_authorizations_expires_at ON retry_authorizations(expires_at)")
        health = repository.check_schema_health()
        self.assertFalse(health.ok)
        self.assertTrue(any("status CHECK" in error for error in health.constraint_errors))

    def test_pseudo_v5_missing_retry_column_fails_health_check(self) -> None:
        repository = SQLiteRuntimeRepository(self.db_path)
        repository.init_schema()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("ALTER TABLE retry_authorizations RENAME TO retry_authorizations_full")
            connection.execute(
                "CREATE TABLE retry_authorizations (retry_authorization_id TEXT PRIMARY KEY)"
            )
        health = repository.check_schema_health()
        self.assertFalse(health.ok)
        self.assertIn("retry_authorizations", health.missing_columns)
        self.assertIn("operation_id", health.missing_columns["retry_authorizations"])

    def test_retry_authorization_constraints_are_enforced(self) -> None:
        repository = SQLiteRuntimeRepository(self.db_path)
        repository.init_schema()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, scope_type, scope_key, action_type, priority, task_status,
                    created_at, updated_at
                ) VALUES ('task-1', 'global', 'scope', 'manual_review', 1, 'pending', ?, ?)
                """,
                (datetime.now().isoformat(), datetime.now().isoformat()),
            )
            connection.execute(
                """
                INSERT INTO shadowbot_operations(
                    operation_id, task_id, platform, expected_old_price, target_price,
                    status, created_at, updated_at
                ) VALUES ('operation-1', 'task-1', 'test', '1', '2', 'pending', ?, ?)
                """,
                (datetime.now().isoformat(), datetime.now().isoformat()),
            )
            connection.execute(
                """
                INSERT INTO shadowbot_execution_attempts(
                    execution_attempt_id, operation_id, execution_mode, status,
                    side_effect_state, started_at
                ) VALUES ('attempt-1', 'operation-1', 'retry', 'started', 'none', ?)
                """,
                (datetime.now().isoformat(),),
            )
            values = (
                "auth-1",
                "operation-1",
                "attempt-1",
                "manual",
                "operator",
                "review",
                "evidence-1",
                "payload-1",
                "ACTIVE",
                1,
                None,
                datetime.now().isoformat(),
                "reason",
                datetime.now().isoformat(),
                None,
            )
            connection.execute(
                """
                INSERT INTO retry_authorizations(
                    retry_authorization_id, operation_id, source_execution_attempt_id,
                    authorization_type, authorized_by, evidence_type, evidence_hash,
                    approved_payload_hash, status, max_uses, consumed_by_execution_attempt_id,
                    expires_at, reason, created_at, consumed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO retry_authorizations SELECT 'auth-2', operation_id, source_execution_attempt_id, authorization_type, authorized_by, evidence_type, evidence_hash, approved_payload_hash, status, max_uses, consumed_by_execution_attempt_id, expires_at, reason, created_at, consumed_at FROM retry_authorizations WHERE retry_authorization_id = 'auth-1'"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE retry_authorizations SET max_uses = 2 WHERE retry_authorization_id = 'auth-1'"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE retry_authorizations SET status = 'UNKNOWN' WHERE retry_authorization_id = 'auth-1'"
                )


if __name__ == "__main__":
    unittest.main()

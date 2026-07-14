from __future__ import annotations

import sqlite3
import tempfile
import unittest
import gc
from datetime import datetime
from pathlib import Path

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION


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

        self.assertEqual(repository.schema_versions(), [1, 2, 3, 4, 5])
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

    def test_v3_and_v4_databases_upgrade_to_v5(self) -> None:
        for version in (3, 4):
            with self.subTest(version=version):
                path = Path(self.temp_dir.name) / f"v{version}.sqlite3"
                with sqlite3.connect(path) as connection:
                    connection.execute(
                        """
                        CREATE TABLE runtime_schema_migrations (
                            schema_version INTEGER PRIMARY KEY,
                            applied_at TEXT NOT NULL,
                            note TEXT NOT NULL DEFAULT ''
                        )
                        """
                    )
                    connection.execute(
                        "INSERT INTO runtime_schema_migrations(schema_version, applied_at, note) VALUES (?, ?, ?)",
                        (version, datetime.now().isoformat(), f"legacy v{version}"),
                    )
                repository = SQLiteRuntimeRepository(path)
                repository.init_schema()
                self.assertEqual(repository.schema_versions(), [1, 2, 3, 4, 5])
                self.assertTrue(repository.check_schema_health().ok)

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

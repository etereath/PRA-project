from __future__ import annotations

import sqlite3
import tempfile
import unittest
import gc
from datetime import datetime
from pathlib import Path

from app.repositories.sqlite_runtime_repository import SCHEMA_SQL, SQLiteRuntimeRepository
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

    @staticmethod
    def _create_historical_database(path: Path, version: int) -> dict[str, str]:
        """Build a pre-v5 database with real tables and sentinel rows.

        The v3 fixture predates the queue-audit columns; v4 has the complete
        pre-v5 ShadowBot tables.  Both fixtures contain the business, review,
        script, and ShadowBot rows that an upgrade must preserve.
        """

        statements = [statement for statement in SCHEMA_SQL if "retry_authorizations" not in statement]
        if version == 3:
            statements = [
                statement.replace("        instruction_hash TEXT NOT NULL DEFAULT '',\n", "")
                .replace("        request_file_sha256 TEXT NOT NULL DEFAULT '',\n", "")
                .replace("        queue_request_path TEXT NOT NULL DEFAULT '',\n", "")
                for statement in statements
            ]

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

    def test_v3_and_v4_databases_upgrade_to_v5(self) -> None:
        for version in (3, 4):
            with self.subTest(version=version):
                path = Path(self.temp_dir.name) / f"v{version}.sqlite3"
                ids = self._create_historical_database(path, version)
                repository = SQLiteRuntimeRepository(path)
                repository.init_schema()
                self.assertEqual(repository.schema_versions(), [1, 2, 3, 4, 5])
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
                    self.assertEqual(
                        connection.execute("SELECT operation_id FROM shadowbot_operations WHERE operation_id = ?", (ids["operation"],)).fetchone()[0],
                        ids["operation"],
                    )
                    self.assertEqual(
                        connection.execute("SELECT execution_attempt_id FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?", (ids["attempt"],)).fetchone()[0],
                        ids["attempt"],
                    )
                repository.init_schema()
                self.assertEqual(repository.schema_versions(), [1, 2, 3, 4, 5])
                self.assertTrue(repository.check_schema_health().ok)

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

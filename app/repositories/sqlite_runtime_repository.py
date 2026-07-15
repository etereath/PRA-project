from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from app.enums import PricingSource, ReviewTaskStatus, TaskActionType, TaskStatus
from app.exceptions import MobileReviewErrorCode, MobileReviewTransactionError
from app.mobile_review import normalize_mobile_review_resolution_payload
from app.models import (
    ExecutionLog,
    MobileReviewAtomicResult,
    NotificationLog,
    RetryAuthorization,
    ReviewTask,
    ReviewToken,
    ScriptRun,
    ScriptRunItem,
    ShadowBotExecutionAttempt,
    ShadowBotOperationLedger,
    ShadowBotSideEffectCheckpoint,
    Task,
    TaskStatusHistory,
)
from app.runtime_schema import (
    LATEST_RUNTIME_SCHEMA_VERSION,
    RuntimeSchemaHealth,
    inspect_runtime_schema,
)
from app.utils import serialize_decimal


TERMINAL_TASK_STATUSES = ("success", "skipped", "cancelled", "expired")

MOBILE_REVIEW_ACTIONS = frozenset({
    ReviewTaskStatus.APPROVED.value,
    ReviewTaskStatus.REJECTED.value,
    ReviewTaskStatus.ADJUSTED.value,
    ReviewTaskStatus.CANCELLED.value,
})

MANUAL_REVIEW_SOURCE_ACTIONS = frozenset({
    TaskActionType.CAPACITY_WARNING,
    TaskActionType.LABOR_REQUIRED,
    TaskActionType.MANUAL_PRICE_REVIEW,
    TaskActionType.BELOW_BREAK_EVEN_REVIEW,
    TaskActionType.SHORTAGE_WARNING,
    TaskActionType.COLD_STORAGE_WARNING,
    TaskActionType.CLEARANCE_WARNING,
    TaskActionType.MANUAL_REVIEW,
})

ATOMIC_TASK_TRANSITIONS = {
    TaskStatus.PENDING: {
        TaskStatus.RUNNING,
        TaskStatus.MANUAL_REVIEW,
        TaskStatus.SKIPPED,
        TaskStatus.CANCELLED,
        TaskStatus.EXPIRED,
    },
    TaskStatus.MANUAL_REVIEW: {
        TaskStatus.PENDING,
        TaskStatus.SKIPPED,
        TaskStatus.CANCELLED,
        TaskStatus.EXPIRED,
    },
}

SQLITE_CONCURRENCY_ERROR_CODES = frozenset(
    getattr(sqlite3, name)
    for name in (
        "SQLITE_BUSY",
        "SQLITE_BUSY_RECOVERY",
        "SQLITE_BUSY_SNAPSHOT",
        "SQLITE_LOCKED",
        "SQLITE_LOCKED_SHAREDCACHE",
        "SQLITE_LOCKED_VTAB",
    )
    if hasattr(sqlite3, name)
)


SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS runtime_schema_migrations (
        schema_version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
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
    CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_open_dedupe
    ON tasks(dedupe_key)
    WHERE dedupe_key <> ''
      AND task_status NOT IN ('success', 'skipped', 'cancelled', 'expired')
    """,
    """
    CREATE TABLE IF NOT EXISTS review_tasks (
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
    CREATE TABLE IF NOT EXISTS review_tokens (
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
    CREATE INDEX IF NOT EXISTS ix_review_tokens_review_task_id
    ON review_tokens(review_task_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_review_tokens_expires_at
    ON review_tokens(expires_at)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_review_tasks_pending_dedupe
    ON review_tasks(dedupe_key)
    WHERE dedupe_key <> '' AND review_status = 'pending'
    """,
    """
    CREATE TABLE IF NOT EXISTS execution_logs (
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
    CREATE TABLE IF NOT EXISTS notification_logs (
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
    CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_logs_dedupe
    ON notification_logs(dedupe_key)
    WHERE dedupe_key <> ''
    """,
    """
    CREATE TABLE IF NOT EXISTS task_status_history (
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
    """
    CREATE TABLE IF NOT EXISTS script_runs (
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
    CREATE TABLE IF NOT EXISTS script_run_items (
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
    CREATE INDEX IF NOT EXISTS ix_script_run_items_script_run_id
    ON script_run_items(script_run_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_script_runs_started_at
    ON script_runs(started_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS shadowbot_operations (
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
    CREATE TABLE IF NOT EXISTS shadowbot_execution_attempts (
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
    CREATE INDEX IF NOT EXISTS ix_shadowbot_execution_attempts_operation_id
    ON shadowbot_execution_attempts(operation_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS shadowbot_side_effect_checkpoints (
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
    """
    CREATE TABLE IF NOT EXISTS retry_authorizations (
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
        FOREIGN KEY(operation_id) REFERENCES shadowbot_operations(operation_id),
        FOREIGN KEY(source_execution_attempt_id) REFERENCES shadowbot_execution_attempts(execution_attempt_id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_retry_authorizations_evidence_hash
    ON retry_authorizations(evidence_hash)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_retry_authorizations_consumed_by_execution_attempt_id
    ON retry_authorizations(consumed_by_execution_attempt_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_retry_authorizations_operation_id
    ON retry_authorizations(operation_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_retry_authorizations_status
    ON retry_authorizations(status)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_retry_authorizations_expires_at
    ON retry_authorizations(expires_at)
    """,
]


class SQLiteRuntimeRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def init_schema(self) -> None:
        with closing(self.connect()) as connection, connection:
            for statement in SCHEMA_SQL:
                connection.execute(statement)
            _ensure_column(connection, "shadowbot_execution_attempts", "instruction_hash", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "shadowbot_execution_attempts", "request_file_sha256", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "shadowbot_execution_attempts", "queue_request_path", "TEXT NOT NULL DEFAULT ''")
            migration_notes = {
                1: "initial runtime schema",
                2: "review token runtime schema",
                3: "business rule evaluation runtime schema",
                4: "shadowbot executor runtime schema",
                5: "retry authorization persistence and shadowbot file queue audit fields",
            }
            for version in range(1, LATEST_RUNTIME_SCHEMA_VERSION + 1):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO runtime_schema_migrations(schema_version, applied_at, note)
                    VALUES (?, ?, ?)
                    """,
                    (version, _datetime_to_text(datetime.now()), migration_notes[version]),
                )
            # Older builds recorded v5 for queue audit columns only.  Keep the
            # applied timestamp stable while correcting the descriptive record
            # so migration history reflects the complete physical v5 shape.
            connection.execute(
                "UPDATE runtime_schema_migrations SET note = ? WHERE schema_version = 5",
                (migration_notes[5],),
            )

    def schema_versions(self) -> list[int]:
        if not self.db_path.exists():
            return []
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT schema_version FROM runtime_schema_migrations ORDER BY schema_version"
            ).fetchall()
        return [int(row["schema_version"]) for row in rows]

    def check_schema_health(self) -> RuntimeSchemaHealth:
        """Return a non-mutating exact-v5 schema health report."""

        if not self.db_path.exists():
            with closing(sqlite3.connect(":memory:")) as connection:
                return inspect_runtime_schema(connection)
        with closing(self.connect()) as connection:
            return inspect_runtime_schema(connection)

    def runtime_schema_health(self) -> RuntimeSchemaHealth:
        """Alias used by operational callers that name the report directly."""

        return self.check_schema_health()

    def health_check(self) -> RuntimeSchemaHealth:
        """Alias used by HTTP/CLI health-check adapters."""

        return self.check_schema_health()

    def insert_tasks(self, tasks: Iterable[Task]) -> int:
        rows = [_task_to_row(task) for task in tasks]
        if not rows:
            return 0
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO tasks(
                    task_id, trade_date, scope_type, scope_key, dedupe_key, internal_sku,
                    platform_name, action_type, priority, task_status, created_at, scheduled_at,
                    expires_at, target_price, target_status, pricing_source, decision_trace_json,
                    result_message, required_by, updated_at
                )
                VALUES(
                    :task_id, :trade_date, :scope_type, :scope_key, :dedupe_key, :internal_sku,
                    :platform_name, :action_type, :priority, :task_status, :created_at, :scheduled_at,
                    :expires_at, :target_price, :target_status, :pricing_source, :decision_trace_json,
                    :result_message, :required_by, :updated_at
                )
                """,
                rows,
            )
            return connection.total_changes - before

    def insert_task(self, task: Task) -> int:
        return self.insert_tasks([task])

    def list_tasks(
        self,
        *,
        trade_date: date | None = None,
        status: TaskStatus | None = None,
        action_type: TaskActionType | None = None,
        scope_type: str | None = None,
        scope_key: str | None = None,
    ) -> list[Task]:
        query = "SELECT * FROM tasks"
        clauses: list[str] = []
        params: list[str] = []
        if trade_date is not None:
            clauses.append("trade_date = ?")
            params.append(trade_date.isoformat())
        if status is not None:
            clauses.append("task_status = ?")
            params.append(status.value)
        if action_type is not None:
            clauses.append("action_type = ?")
            params.append(action_type.value)
        if scope_type:
            clauses.append("scope_type = ?")
            params.append(scope_type)
        if scope_key:
            clauses.append("scope_key = ?")
            params.append(scope_key)
        if clauses:
            query = f"{query} WHERE {' AND '.join(clauses)}"
        query = f"{query} ORDER BY priority ASC, created_at ASC, task_id ASC"
        with closing(self.connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_task(row) for row in rows]

    def get_task(self, task_id: str) -> Task | None:
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return _row_to_task(row) if row is not None else None

    def get_open_task_by_dedupe_key(self, dedupe_key: str) -> Task | None:
        if not dedupe_key:
            return None
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM tasks
                WHERE dedupe_key = ?
                  AND task_status NOT IN ('success', 'skipped', 'cancelled', 'expired')
                ORDER BY created_at DESC, task_id ASC
                LIMIT 1
                """,
                (dedupe_key,),
            ).fetchone()
        return _row_to_task(row) if row is not None else None

    def update_task_status(self, task_id: str, status: TaskStatus, *, result_message: str = "") -> None:
        updated_at = _datetime_to_text(datetime.now())
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE tasks
                SET task_status = ?, result_message = COALESCE(NULLIF(?, ''), result_message), updated_at = ?
                WHERE task_id = ?
                """,
                (status.value, result_message, updated_at, task_id),
            )

    def update_task_status_with_history(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        history: TaskStatusHistory,
        result_message: str = "",
    ) -> None:
        updated_at = _datetime_to_text(datetime.now())
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE tasks
                SET task_status = ?, result_message = COALESCE(NULLIF(?, ''), result_message), updated_at = ?
                WHERE task_id = ?
                """,
                (status.value, result_message, updated_at, task_id),
            )
            connection.execute(
                """
                INSERT INTO task_status_history(
                    history_id, task_id, from_status, to_status, changed_by, changed_at, reason, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history.history_id,
                    history.task_id,
                    history.from_status.value if history.from_status is not None else None,
                    history.to_status.value,
                    history.changed_by,
                    _datetime_to_text(history.changed_at),
                    history.reason,
                    _json_dump(history.metadata),
                ),
            )

    def insert_status_history(self, history: TaskStatusHistory) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO task_status_history(
                    history_id, task_id, from_status, to_status, changed_by, changed_at, reason, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history.history_id,
                    history.task_id,
                    history.from_status.value if history.from_status else None,
                    history.to_status.value,
                    history.changed_by,
                    _datetime_to_text(history.changed_at),
                    history.reason,
                    _json_dump(history.metadata),
                ),
            )

    def list_task_status_history(self, task_id: str) -> list[TaskStatusHistory]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM task_status_history
                WHERE task_id = ?
                ORDER BY changed_at ASC, history_id ASC
                """,
                (task_id,),
            ).fetchall()
        return [_row_to_status_history(row) for row in rows]

    def insert_review_tasks(self, review_tasks: Iterable[ReviewTask]) -> int:
        rows = [_review_task_to_row(review_task) for review_task in review_tasks]
        if not rows:
            return 0
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO review_tasks(
                    review_task_id, trade_date, scope_type, scope_key, dedupe_key, source_task_id,
                    review_type, review_status, internal_sku, platform_name, reason, review_payload_json,
                    resolution_payload_json, required_by, created_at, updated_at, resolved_by,
                    resolved_at, resolution_note
                )
                VALUES(
                    :review_task_id, :trade_date, :scope_type, :scope_key, :dedupe_key, :source_task_id,
                    :review_type, :review_status, :internal_sku, :platform_name, :reason, :review_payload_json,
                    :resolution_payload_json, :required_by, :created_at, :updated_at, :resolved_by,
                    :resolved_at, :resolution_note
                )
                """,
                rows,
            )
            return connection.total_changes - before

    def list_review_tasks(
        self,
        *,
        trade_date: date | None = None,
        status: ReviewTaskStatus | None = None,
    ) -> list[ReviewTask]:
        query = "SELECT * FROM review_tasks"
        clauses: list[str] = []
        params: list[str] = []
        if trade_date is not None:
            clauses.append("trade_date = ?")
            params.append(trade_date.isoformat())
        if status is not None:
            clauses.append("review_status = ?")
            params.append(status.value)
        if clauses:
            query = f"{query} WHERE {' AND '.join(clauses)}"
        query = f"{query} ORDER BY required_by IS NULL, required_by ASC, created_at ASC"
        with closing(self.connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_review_task(row) for row in rows]

    def get_review_task(self, review_task_id: str) -> ReviewTask | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM review_tasks WHERE review_task_id = ?",
                (review_task_id,),
            ).fetchone()
        return _row_to_review_task(row) if row is not None else None

    def get_pending_review_task_by_dedupe_key(self, dedupe_key: str) -> ReviewTask | None:
        if not dedupe_key:
            return None
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM review_tasks
                WHERE dedupe_key = ? AND review_status = 'pending'
                ORDER BY created_at DESC, review_task_id ASC
                LIMIT 1
                """,
                (dedupe_key,),
            ).fetchone()
        return _row_to_review_task(row) if row is not None else None

    def list_pending_review_tasks_due_before(self, cutoff: datetime) -> list[ReviewTask]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM review_tasks
                WHERE review_status = 'pending'
                  AND required_by IS NOT NULL
                  AND required_by < ?
                ORDER BY required_by ASC, created_at ASC
                """,
                (_datetime_to_text(cutoff),),
            ).fetchall()
        return [_row_to_review_task(row) for row in rows]

    def update_review_task(self, review_task: ReviewTask) -> None:
        row = _review_task_to_row(review_task)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE review_tasks
                SET review_status = :review_status,
                    resolution_payload_json = :resolution_payload_json,
                    updated_at = :updated_at,
                    resolved_by = :resolved_by,
                    resolved_at = :resolved_at,
                    resolution_note = :resolution_note
                WHERE review_task_id = :review_task_id
                """,
                row,
            )

    def update_review_task_with_optional_task_status(
        self,
        review_task: ReviewTask,
        *,
        task_id: str | None = None,
        task_status: TaskStatus | None = None,
        history: TaskStatusHistory | None = None,
        result_message: str = "",
    ) -> None:
        review_row = _review_task_to_row(review_task)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE review_tasks
                SET review_status = :review_status,
                    resolution_payload_json = :resolution_payload_json,
                    updated_at = :updated_at,
                    resolved_by = :resolved_by,
                    resolved_at = :resolved_at,
                    resolution_note = :resolution_note
                WHERE review_task_id = :review_task_id
                """,
                review_row,
            )
            if task_id is None or task_status is None or history is None:
                return
            updated_at = _datetime_to_text(datetime.now())
            connection.execute(
                """
                UPDATE tasks
                SET task_status = ?, result_message = COALESCE(NULLIF(?, ''), result_message), updated_at = ?
                WHERE task_id = ?
                """,
                (task_status.value, result_message, updated_at, task_id),
            )
            connection.execute(
                """
                INSERT INTO task_status_history(
                    history_id, task_id, from_status, to_status, changed_by, changed_at, reason, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history.history_id,
                    history.task_id,
                    history.from_status.value if history.from_status is not None else None,
                    history.to_status.value,
                    history.changed_by,
                    _datetime_to_text(history.changed_at),
                    history.reason,
                    _json_dump(history.metadata),
                ),
            )

    def resolve_mobile_review_atomic(
        self,
        *,
        review_task_id: str,
        token_hash: str,
        status: ReviewTaskStatus,
        actor_source: str,
        actor: str | None = None,
        note: str = "",
        resolution_payload: dict[str, object] | None = None,
        now: datetime | None = None,
        failure_injector: Callable[[str], None] | None = None,
    ) -> MobileReviewAtomicResult:
        """Resolve Mobile Review state in one SQLite transaction.

        Parsing and payload-shape validation belong before this method. Every
        state decision here is made from rows read through the same connection
        after ``BEGIN IMMEDIATE``.
        """

        timestamp = now or datetime.now()
        payload = normalize_mobile_review_resolution_payload(status, resolution_payload)

        def inject(point: str) -> None:
            if failure_injector is not None:
                failure_injector(point)

        with closing(self.connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                token_row = connection.execute(
                    "SELECT * FROM review_tokens WHERE token_hash = ?",
                    (token_hash,),
                ).fetchone()
                if token_row is None:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.TOKEN_NOT_FOUND,
                        "链接已失效或无权访问该复核任务",
                    )
                if str(token_row["review_task_id"]) != review_task_id:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.TOKEN_REVIEW_MISMATCH,
                        "链接已失效或无权访问该复核任务",
                    )

                review_row = connection.execute(
                    "SELECT * FROM review_tasks WHERE review_task_id = ?",
                    (review_task_id,),
                ).fetchone()
                if review_row is None:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.REVIEW_NOT_FOUND,
                        "链接已失效或无权访问该复核任务",
                    )

                expires_at = _text_to_datetime(token_row["expires_at"])
                if expires_at is not None and expires_at <= timestamp:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.TOKEN_EXPIRED,
                        "链接已失效或无权访问该复核任务",
                    )
                if token_row["revoked_at"] is not None:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.TOKEN_REVOKED,
                        "链接已失效或无权访问该复核任务",
                    )
                if token_row["used_at"] is not None:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.TOKEN_ALREADY_USED,
                        "链接已失效或无权访问该复核任务",
                    )
                if str(review_row["review_status"]) != ReviewTaskStatus.PENDING.value:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.REVIEW_ALREADY_RESOLVED,
                        "链接已失效或无权访问该复核任务",
                    )

                action = status.value
                allowed_actions = _json_list_load(token_row["allowed_actions"])
                if action not in allowed_actions:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.ACTION_NOT_ALLOWED,
                        "链接已失效或无权访问该复核任务",
                    )
                if action not in MOBILE_REVIEW_ACTIONS:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.ACTION_NOT_ALLOWED_FOR_REVIEW_TYPE,
                        "链接已失效或无权访问该复核任务",
                    )

                source_task_id = review_row["source_task_id"]
                if not source_task_id:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.SOURCE_TASK_NOT_FOUND,
                        "关联源任务不存在或已失效",
                    )
                source_row = (
                    connection.execute(
                        "SELECT * FROM tasks WHERE task_id = ?",
                        (source_task_id,),
                    ).fetchone()
                    if source_task_id
                    else None
                )
                if source_row is None:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.SOURCE_TASK_NOT_FOUND,
                        "关联源任务不存在或已失效",
                    )
                source_task_status = _atomic_source_task_status(source_row, status)
                if source_task_status is None:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.CONCURRENT_UPDATE,
                        "关联源任务状态已变化，复核请求未提交",
                    )
                resolved_actor = actor or str(token_row["token_subject"])
                adjustment = payload.get("adjustment") if status == ReviewTaskStatus.ADJUSTED else None
                adjusted_target_price = None
                adjusted_target_status = None
                adjusted_result_message = note
                adjusted_decision_trace_json = None
                if isinstance(adjustment, dict):
                    adjusted_target_price = adjustment.get("target_price")
                    adjusted_target_status = adjustment.get("target_status")
                    adjusted_result_message = str(adjustment.get("result_message") or note)
                    if source_row is not None:
                        decision_trace = _json_load(source_row["decision_trace_json"])
                        decision_trace["mobile_review_adjustment"] = adjustment
                        adjusted_decision_trace_json = _json_dump(decision_trace)

                token_updated = connection.execute(
                    """
                    UPDATE review_tokens
                    SET used_at = ?, last_used_at = ?
                    WHERE token_id = ?
                      AND review_task_id = ?
                      AND token_hash = ?
                      AND allowed_actions = ?
                      AND used_at IS NULL
                      AND revoked_at IS NULL
                      AND expires_at > ?
                    """,
                    (
                        _datetime_to_text(timestamp),
                        _datetime_to_text(timestamp),
                        token_row["token_id"],
                        review_task_id,
                        token_hash,
                        token_row["allowed_actions"],
                        _datetime_to_text(timestamp),
                    ),
                ).rowcount
                if token_updated != 1:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.CONCURRENT_UPDATE,
                        "复核请求发生并发更新，请重试",
                    )
                inject("after_token_update")

                review_updated = connection.execute(
                    """
                    UPDATE review_tasks
                    SET review_status = ?,
                        resolution_payload_json = ?,
                        updated_at = ?,
                        resolved_by = ?,
                        resolved_at = ?,
                        resolution_note = ?
                    WHERE review_task_id = ?
                      AND review_status = ?
                    """,
                    (
                        action,
                        _json_dump(payload),
                        _datetime_to_text(timestamp),
                        resolved_actor,
                        _datetime_to_text(timestamp),
                        note,
                        review_task_id,
                        ReviewTaskStatus.PENDING.value,
                    ),
                ).rowcount
                if review_updated != 1:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.REVIEW_ALREADY_RESOLVED,
                        "复核任务已被其他请求处理",
                    )
                inject("after_review_update")

                if source_row is not None and source_task_status is not None:
                    current_source_status = TaskStatus(str(source_row["task_status"]))
                    if source_task_status != current_source_status and source_task_status not in ATOMIC_TASK_TRANSITIONS.get(
                        current_source_status, set()
                    ):
                        raise MobileReviewTransactionError(
                            MobileReviewErrorCode.CONCURRENT_UPDATE,
                            "源任务状态已变化，复核请求未提交",
                        )
                    task_updated = connection.execute(
                        """
                        UPDATE tasks
                        SET task_status = ?,
                            target_price = COALESCE(?, target_price),
                            target_status = COALESCE(?, target_status),
                            decision_trace_json = COALESCE(?, decision_trace_json),
                            result_message = COALESCE(NULLIF(?, ''), result_message),
                            updated_at = ?
                        WHERE task_id = ?
                          AND task_status = ?
                        """,
                        (
                            source_task_status.value,
                            adjusted_target_price,
                            adjusted_target_status,
                            adjusted_decision_trace_json,
                            adjusted_result_message,
                            _datetime_to_text(timestamp),
                            source_task_id,
                            current_source_status.value,
                        ),
                    ).rowcount
                    if task_updated != 1:
                        raise MobileReviewTransactionError(
                            MobileReviewErrorCode.CONCURRENT_UPDATE,
                            "源任务状态已变化，复核请求未提交",
                        )
                    inject("after_task_update")

                    history_metadata = {
                        "review_task_id": review_task_id,
                        "review_status": status.value,
                        "actor": resolved_actor,
                        "actor_source": actor_source,
                        "resolution_note": note,
                        "resolution_payload_summary": _resolution_payload_summary(payload),
                    }
                    history_id = uuid4().hex[:12]
                    inject("before_history_insert")
                    connection.execute(
                        """
                        INSERT INTO task_status_history(
                            history_id, task_id, from_status, to_status, changed_by, changed_at, reason, metadata_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            history_id,
                            source_task_id,
                            current_source_status.value,
                            source_task_status.value,
                            resolved_actor,
                            _datetime_to_text(timestamp),
                            f"review_task:{review_task_id}:{status.value}",
                            _json_dump(history_metadata),
                        ),
                    )
                    inject("after_history_insert")

                committed_review_row = connection.execute(
                    "SELECT * FROM review_tasks WHERE review_task_id = ?",
                    (review_task_id,),
                ).fetchone()
                committed_token_row = connection.execute(
                    "SELECT * FROM review_tokens WHERE token_id = ?",
                    (token_row["token_id"],),
                ).fetchone()
                committed_source_row = (
                    connection.execute(
                        "SELECT * FROM tasks WHERE task_id = ?",
                        (source_task_id,),
                    ).fetchone()
                    if source_task_id
                    else None
                )
                if committed_review_row is None or committed_token_row is None:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.CONCURRENT_UPDATE,
                        "复核结果提交前读取失败",
                    )
                inject("before_result_conversion")
                result = MobileReviewAtomicResult(
                    review_task=_row_to_review_task(committed_review_row),
                    review_token=_row_to_review_token(committed_token_row),
                    source_task=_row_to_task(committed_source_row) if committed_source_row is not None else None,
                    source_task_status=source_task_status,
                )
                connection.commit()
                return result
            except MobileReviewTransactionError:
                connection.rollback()
                raise
            except sqlite3.OperationalError as exc:
                connection.rollback()
                if _is_sqlite_concurrency_error(exc):
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.CONCURRENT_UPDATE,
                        "复核请求发生并发更新，请重试",
                    ) from exc
                raise
            except Exception:
                connection.rollback()
                raise

    def insert_review_token(self, review_token: ReviewToken) -> int:
        row = _review_token_to_row(review_token)
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO review_tokens(
                    token_id, review_task_id, token_hash, token_subject, allowed_actions,
                    expires_at, used_at, revoked_at, created_at, created_by, last_used_at, note
                )
                VALUES(
                    :token_id, :review_task_id, :token_hash, :token_subject, :allowed_actions,
                    :expires_at, :used_at, :revoked_at, :created_at, :created_by, :last_used_at, :note
                )
                """,
                row,
            )
            return connection.total_changes - before

    def get_review_token(self, token_id: str) -> ReviewToken | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM review_tokens WHERE token_id = ?",
                (token_id,),
            ).fetchone()
        return _row_to_review_token(row) if row is not None else None

    def get_review_token_by_hash(self, token_hash: str) -> ReviewToken | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM review_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        return _row_to_review_token(row) if row is not None else None

    def list_review_tokens_by_review_task_id(self, review_task_id: str) -> list[ReviewToken]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM review_tokens
                WHERE review_task_id = ?
                ORDER BY created_at ASC, token_id ASC
                """,
                (review_task_id,),
            ).fetchall()
        return [_row_to_review_token(row) for row in rows]

    def update_review_token_usage(
        self,
        token_id: str,
        *,
        used_at: datetime | None = None,
        last_used_at: datetime | None = None,
    ) -> None:
        assignments: list[str] = []
        params: list[str] = []
        if used_at is not None:
            assignments.append("used_at = ?")
            params.append(_datetime_to_text(used_at) or "")
        if last_used_at is not None:
            assignments.append("last_used_at = ?")
            params.append(_datetime_to_text(last_used_at) or "")
        if not assignments:
            return
        params.append(token_id)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                f"UPDATE review_tokens SET {', '.join(assignments)} WHERE token_id = ?",
                params,
            )

    def revoke_review_token(self, token_id: str, revoked_at: datetime) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "UPDATE review_tokens SET revoked_at = ? WHERE token_id = ?",
                (_datetime_to_text(revoked_at), token_id),
            )

    def revoke_review_tokens_by_review_task_id(self, review_task_id: str, revoked_at: datetime) -> int:
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.execute(
                """
                UPDATE review_tokens
                SET revoked_at = ?
                WHERE review_task_id = ? AND revoked_at IS NULL
                """,
                (_datetime_to_text(revoked_at), review_task_id),
            )
            return connection.total_changes - before

    def insert_execution_logs(self, logs: Iterable[ExecutionLog]) -> int:
        rows = [_execution_log_to_row(log) for log in logs]
        if not rows:
            return 0
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO execution_logs(
                    log_id, task_id, executor_name, start_time, end_time, success_flag,
                    error_code, error_message, raw_output, ai_model_version, ai_summary, created_at
                )
                VALUES(
                    :log_id, :task_id, :executor_name, :start_time, :end_time, :success_flag,
                    :error_code, :error_message, :raw_output, :ai_model_version, :ai_summary, :created_at
                )
                """,
                rows,
            )
            return connection.total_changes - before

    def list_execution_logs(self, *, task_id: str | None = None, limit: int | None = None) -> list[ExecutionLog]:
        query = "SELECT * FROM execution_logs"
        params: list[object] = []
        if task_id:
            query = f"{query} WHERE task_id = ?"
            params.append(task_id)
        query = f"{query} ORDER BY created_at DESC, log_id ASC"
        if limit is not None:
            query = f"{query} LIMIT ?"
            params.append(limit)
        with closing(self.connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_execution_log(row) for row in rows]

    def insert_shadowbot_operation(self, operation: ShadowBotOperationLedger) -> int:
        row = _shadowbot_operation_to_row(operation)
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO shadowbot_operations(
                    operation_id, task_id, platform, product_identity_json, expected_old_price,
                    target_price, status, lock_owner, approved_payload_hash, created_at, updated_at
                )
                VALUES(
                    :operation_id, :task_id, :platform, :product_identity_json, :expected_old_price,
                    :target_price, :status, :lock_owner, :approved_payload_hash, :created_at, :updated_at
                )
                """,
                row,
            )
            return connection.total_changes - before

    def get_shadowbot_operation(self, operation_id: str) -> ShadowBotOperationLedger | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM shadowbot_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return _row_to_shadowbot_operation(row) if row is not None else None

    def acquire_shadowbot_operation_lock(self, operation_id: str, lock_owner: str) -> bool:
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE shadowbot_operations
                SET lock_owner = ?, updated_at = ?
                WHERE operation_id = ? AND (lock_owner = '' OR lock_owner = ?)
                """,
                (lock_owner, _datetime_to_text(datetime.now()), operation_id, lock_owner),
            )
            return cursor.rowcount == 1

    def update_shadowbot_operation_status(self, operation_id: str, status: str, *, lock_owner: str | None = None) -> None:
        assignments = ["status = ?", "updated_at = ?"]
        params: list[object] = [status, _datetime_to_text(datetime.now())]
        if lock_owner is not None:
            assignments.append("lock_owner = ?")
            params.append(lock_owner)
        params.append(operation_id)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                f"UPDATE shadowbot_operations SET {', '.join(assignments)} WHERE operation_id = ?",
                params,
            )

    def insert_shadowbot_execution_attempt(self, attempt: ShadowBotExecutionAttempt) -> int:
        row = _shadowbot_attempt_to_row(attempt)
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO shadowbot_execution_attempts(
                    execution_attempt_id, operation_id, execution_mode, shadowbot_run_id,
                    status, side_effect_state, started_at, instruction_hash,
                    request_file_sha256, queue_request_path, ended_at, raw_output_json
                )
                VALUES(
                    :execution_attempt_id, :operation_id, :execution_mode, :shadowbot_run_id,
                    :status, :side_effect_state, :started_at, :instruction_hash,
                    :request_file_sha256, :queue_request_path, :ended_at, :raw_output_json
                )
                """,
                row,
            )
            return connection.total_changes - before

    def get_shadowbot_execution_attempt(self, execution_attempt_id: str) -> ShadowBotExecutionAttempt | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?",
                (execution_attempt_id,),
            ).fetchone()
        return _row_to_shadowbot_attempt(row) if row is not None else None

    def list_shadowbot_execution_attempts(self, *, operation_id: str) -> list[ShadowBotExecutionAttempt]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM shadowbot_execution_attempts
                WHERE operation_id = ?
                ORDER BY started_at, execution_attempt_id
                """,
                (operation_id,),
            ).fetchall()
        return [_row_to_shadowbot_attempt(row) for row in rows]

    def list_active_shadowbot_execution_attempts(self) -> list[ShadowBotExecutionAttempt]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM shadowbot_execution_attempts
                WHERE status IN ('STARTING', 'RUNNING')
                ORDER BY operation_id, started_at, execution_attempt_id
                """
            ).fetchall()
        return [_row_to_shadowbot_attempt(row) for row in rows]

    def freeze_duplicate_active_commit_attempts(self, operation_id: str, *, now: datetime) -> bool:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM shadowbot_execution_attempts
                WHERE operation_id = ? AND execution_mode = 'COMMIT' AND status IN ('STARTING', 'RUNNING')
                ORDER BY started_at, execution_attempt_id
                """,
                (operation_id,),
            ).fetchall()
            if len(rows) < 2:
                connection.rollback()
                return False
            for row in rows:
                raw = _json_load(row["raw_output_json"])
                raw["frozen_reason"] = "DUPLICATE_ACTIVE_COMMIT_ATTEMPT"
                raw["frozen_at"] = _datetime_to_text(now)
                lease = raw.get("lease") if isinstance(raw.get("lease"), dict) else {}
                lease["active"] = False
                lease["frozen_at"] = _datetime_to_text(now)
                raw["lease"] = lease
                checkpoint = connection.execute(
                    """
                    SELECT side_effect_state FROM shadowbot_side_effect_checkpoints
                    WHERE execution_attempt_id = ? ORDER BY version DESC LIMIT 1
                    """,
                    (str(row["execution_attempt_id"]),),
                ).fetchone()
                observed_side_effect = (
                    str(checkpoint["side_effect_state"])
                    if checkpoint is not None
                    else str(row["side_effect_state"])
                )
                start_unknown = str(row["status"]) == "STARTING" and observed_side_effect == "NOT_STARTED"
                attempt_status = "START_UNKNOWN" if start_unknown else "SIDE_EFFECT_UNKNOWN"
                terminal_side_effect = "NOT_STARTED" if start_unknown else "UNKNOWN"
                connection.execute(
                    """
                    UPDATE shadowbot_execution_attempts
                    SET status = ?, side_effect_state = ?, ended_at = ?, raw_output_json = ?
                    WHERE execution_attempt_id = ? AND status IN ('STARTING', 'RUNNING')
                    """,
                    (
                        attempt_status,
                        terminal_side_effect,
                        _datetime_to_text(now),
                        _json_dump(raw),
                        str(row["execution_attempt_id"]),
                    ),
                )
            connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = 'MANUAL_REVIEW', lock_owner = '', updated_at = ?
                WHERE operation_id = ?
                """,
                (_datetime_to_text(now), operation_id),
            )
            connection.commit()
            return True
        finally:
            connection.close()

    def create_shadowbot_attempt_with_lease(
        self,
        attempt: ShadowBotExecutionAttempt,
        *,
        owner_token: str,
        lease_expires_at: datetime,
        expected_operation_statuses: Iterable[str],
    ) -> ShadowBotExecutionAttempt | None:
        """Atomically bind a fresh attempt, lease and RUNNING operation."""
        expected = tuple(dict.fromkeys(str(value) for value in expected_operation_statuses))
        if not expected:
            raise ValueError("expected_operation_statuses must not be empty")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT status, lock_owner FROM shadowbot_operations WHERE operation_id = ?",
                (attempt.operation_id,),
            ).fetchone()
            if operation is None or str(operation["status"]) not in expected or str(operation["lock_owner"] or ""):
                connection.rollback()
                return None
            active = connection.execute(
                """
                SELECT 1 FROM shadowbot_execution_attempts
                WHERE operation_id = ? AND execution_mode = 'COMMIT' AND status IN ('STARTING', 'RUNNING')
                LIMIT 1
                """,
                (attempt.operation_id,),
            ).fetchone()
            if active is not None:
                connection.rollback()
                return None
            lease_version = self._next_shadowbot_lease_version(connection, attempt.operation_id)
            attempt.raw_output = {
                **attempt.raw_output,
                "operation_status_before_attempt": str(operation["status"]),
                "lease": {
                    "owner_token": owner_token,
                    "version": lease_version,
                    "expires_at": _datetime_to_text(lease_expires_at),
                    "active": True,
                },
            }
            connection.execute(
                """
                INSERT INTO shadowbot_execution_attempts(
                    execution_attempt_id, operation_id, execution_mode, shadowbot_run_id,
                    status, side_effect_state, started_at, instruction_hash,
                    request_file_sha256, queue_request_path, ended_at, raw_output_json
                ) VALUES(
                    :execution_attempt_id, :operation_id, :execution_mode, :shadowbot_run_id,
                    :status, :side_effect_state, :started_at, :instruction_hash,
                    :request_file_sha256, :queue_request_path, :ended_at, :raw_output_json
                )
                """,
                _shadowbot_attempt_to_row(attempt),
            )
            placeholders = ",".join("?" for _ in expected)
            cursor = connection.execute(
                f"""
                UPDATE shadowbot_operations
                SET status = 'RUNNING', lock_owner = ?, updated_at = ?
                WHERE operation_id = ? AND status IN ({placeholders}) AND lock_owner = ''
                """,
                (owner_token, _datetime_to_text(datetime.now()), attempt.operation_id, *expected),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
            return attempt
        except sqlite3.IntegrityError:
            connection.rollback()
            return None
        finally:
            connection.close()

    def mark_shadowbot_start_outcome(
        self,
        execution_attempt_id: str,
        *,
        owner_token: str,
        lease_version: int,
        attempt_status: str,
        operation_status: str,
        shadowbot_run_id: str = "",
        instruction_hash: str = "",
        request_file_sha256: str = "",
        queue_request_path: str = "",
        raw_output: dict[str, Any] | None = None,
        ended_at: datetime | None = None,
    ) -> bool:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT a.*, o.lock_owner
                FROM shadowbot_execution_attempts a
                JOIN shadowbot_operations o ON o.operation_id = a.operation_id
                WHERE a.execution_attempt_id = ?
                """,
                (execution_attempt_id,),
            ).fetchone()
            if row is None or str(row["lock_owner"] or "") != owner_token:
                connection.rollback()
                return False
            current_raw = _json_load(row["raw_output_json"])
            lease = current_raw.get("lease") if isinstance(current_raw.get("lease"), dict) else {}
            lease_expires_at = _text_to_datetime(lease.get("expires_at"))
            lease_now = datetime.now(lease_expires_at.tzinfo) if lease_expires_at is not None else None
            if (
                str(lease.get("owner_token") or "") != owner_token
                or int(lease.get("version") or 0) != lease_version
                or not bool(lease.get("active", False))
                or lease_expires_at is None
                or lease_now is None
                or lease_expires_at <= lease_now
            ):
                connection.rollback()
                return False
            merged_raw = {**current_raw, **(raw_output or {})}
            merged_lease = dict(lease)
            terminal = attempt_status not in {"STARTING", "RUNNING"}
            if terminal:
                merged_lease["active"] = False
                merged_lease["ended_at"] = _datetime_to_text(ended_at or datetime.now())
            merged_raw["lease"] = merged_lease
            connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET shadowbot_run_id = ?, status = ?, instruction_hash = ?,
                    request_file_sha256 = ?, queue_request_path = ?, ended_at = ?, raw_output_json = ?
                WHERE execution_attempt_id = ?
                """,
                (
                    shadowbot_run_id,
                    attempt_status,
                    instruction_hash,
                    request_file_sha256,
                    queue_request_path,
                    _datetime_to_text(ended_at),
                    _json_dump(merged_raw),
                    execution_attempt_id,
                ),
            )
            connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = ?, lock_owner = ?, updated_at = ?
                WHERE operation_id = ? AND lock_owner = ?
                """,
                (
                    operation_status,
                    "" if terminal else owner_token,
                    _datetime_to_text(datetime.now()),
                    str(row["operation_id"]),
                    owner_token,
                ),
            )
            connection.commit()
            return True
        finally:
            connection.close()

    def validate_shadowbot_lease(
        self,
        execution_attempt_id: str,
        *,
        owner_token: str,
        lease_version: int,
        now: datetime,
    ) -> bool:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT a.raw_output_json, o.lock_owner
                FROM shadowbot_execution_attempts a
                JOIN shadowbot_operations o ON o.operation_id = a.operation_id
                WHERE a.execution_attempt_id = ?
                """,
                (execution_attempt_id,),
            ).fetchone()
        if row is None or str(row["lock_owner"] or "") != owner_token:
            return False
        raw = _json_load(row["raw_output_json"])
        lease = raw.get("lease") if isinstance(raw.get("lease"), dict) else {}
        expires_at = _text_to_datetime(lease.get("expires_at"))
        return bool(
            lease.get("active", False)
            and str(lease.get("owner_token") or "") == owner_token
            and int(lease.get("version") or 0) == lease_version
            and expires_at is not None
            and expires_at > now
        )

    def complete_shadowbot_attempt_with_lease(
        self,
        execution_attempt_id: str,
        *,
        owner_token: str,
        lease_version: int,
        attempt_status: str,
        operation_status: str,
        side_effect_state: str,
        ended_at: datetime,
        raw_output: dict[str, Any],
    ) -> bool:
        """Fence result writeback with owner/version and close lease atomically."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT a.*, o.lock_owner
                FROM shadowbot_execution_attempts a
                JOIN shadowbot_operations o ON o.operation_id = a.operation_id
                WHERE a.execution_attempt_id = ? AND a.status IN ('STARTING', 'RUNNING')
                """,
                (execution_attempt_id,),
            ).fetchone()
            if row is None or str(row["lock_owner"] or "") != owner_token:
                connection.rollback()
                return False
            current_raw = _json_load(row["raw_output_json"])
            lease = current_raw.get("lease") if isinstance(current_raw.get("lease"), dict) else {}
            expires_at = _text_to_datetime(lease.get("expires_at"))
            if (
                not bool(lease.get("active", False))
                or str(lease.get("owner_token") or "") != owner_token
                or int(lease.get("version") or 0) != lease_version
                or expires_at is None
                or expires_at <= ended_at
            ):
                connection.rollback()
                return False
            lease["active"] = False
            lease["ended_at"] = _datetime_to_text(ended_at)
            merged_raw = {**current_raw, **raw_output, "lease": lease}
            next_version = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                FROM shadowbot_side_effect_checkpoints WHERE operation_id = ?
                """,
                (str(row["operation_id"]),),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO shadowbot_side_effect_checkpoints(
                    operation_id, execution_attempt_id, side_effect_state, checkpoint_at, version
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(row["operation_id"]),
                    execution_attempt_id,
                    side_effect_state,
                    _datetime_to_text(ended_at),
                    int(next_version["next_version"]),
                ),
            )
            connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET status = ?, side_effect_state = ?, ended_at = ?, raw_output_json = ?
                WHERE execution_attempt_id = ?
                """,
                (
                    attempt_status,
                    side_effect_state,
                    _datetime_to_text(ended_at),
                    _json_dump(merged_raw),
                    execution_attempt_id,
                ),
            )
            updated = connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = ?, lock_owner = '', updated_at = ?
                WHERE operation_id = ? AND lock_owner = ?
                """,
                (operation_status, _datetime_to_text(ended_at), str(row["operation_id"]), owner_token),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
            return True
        finally:
            connection.close()

    def renew_shadowbot_lease(
        self,
        execution_attempt_id: str,
        *,
        owner_token: str,
        lease_version: int,
        now: datetime,
        new_expires_at: datetime,
    ) -> bool:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT a.raw_output_json, a.operation_id, o.lock_owner
                FROM shadowbot_execution_attempts a
                JOIN shadowbot_operations o ON o.operation_id = a.operation_id
                WHERE a.execution_attempt_id = ? AND a.status IN ('STARTING', 'RUNNING')
                """,
                (execution_attempt_id,),
            ).fetchone()
            if row is None or str(row["lock_owner"] or "") != owner_token:
                connection.rollback()
                return False
            raw = _json_load(row["raw_output_json"])
            lease = raw.get("lease") if isinstance(raw.get("lease"), dict) else {}
            current_expires = _text_to_datetime(lease.get("expires_at"))
            if (
                not bool(lease.get("active", False))
                or str(lease.get("owner_token") or "") != owner_token
                or int(lease.get("version") or 0) != lease_version
                or current_expires is None
                or current_expires <= now
            ):
                connection.rollback()
                return False
            lease["expires_at"] = _datetime_to_text(new_expires_at)
            raw["lease"] = lease
            cursor = connection.execute(
                "UPDATE shadowbot_execution_attempts SET raw_output_json = ? WHERE execution_attempt_id = ?",
                (_json_dump(raw), execution_attempt_id),
            )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            connection.close()

    def expire_shadowbot_lease(self, execution_attempt_id: str, *, now: datetime) -> bool:
        """F10: fence a stale owner and move the operation to reconciliation."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT a.*, o.lock_owner
                FROM shadowbot_execution_attempts a
                JOIN shadowbot_operations o ON o.operation_id = a.operation_id
                WHERE a.execution_attempt_id = ? AND a.status IN ('STARTING', 'RUNNING')
                """,
                (execution_attempt_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            raw = _json_load(row["raw_output_json"])
            lease = raw.get("lease") if isinstance(raw.get("lease"), dict) else {}
            expires_at = _text_to_datetime(lease.get("expires_at"))
            if not bool(lease.get("active", False)) or expires_at is None or expires_at > now:
                connection.rollback()
                return False
            if str(row["lock_owner"] or "") != str(lease.get("owner_token") or ""):
                connection.rollback()
                return False
            lease["active"] = False
            lease["expired_at"] = _datetime_to_text(now)
            raw["lease"] = lease
            attempt_status = "START_UNKNOWN" if str(row["status"]) == "STARTING" else "SIDE_EFFECT_UNKNOWN"
            connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET status = ?, side_effect_state = 'UNKNOWN', ended_at = ?, raw_output_json = ?
                WHERE execution_attempt_id = ?
                """,
                (attempt_status, _datetime_to_text(now), _json_dump(raw), execution_attempt_id),
            )
            connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = 'NEEDS_RECONCILIATION', lock_owner = '', updated_at = ?
                WHERE operation_id = ? AND lock_owner = ?
                """,
                (_datetime_to_text(now), str(row["operation_id"]), str(lease.get("owner_token") or "")),
            )
            connection.commit()
            return True
        finally:
            connection.close()

    def quarantine_shadowbot_attempt(
        self,
        execution_attempt_id: str,
        *,
        reason: str,
        now: datetime,
    ) -> bool:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?",
                (execution_attempt_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            raw = _json_load(row["raw_output_json"])
            lease = raw.get("lease") if isinstance(raw.get("lease"), dict) else {}
            lease["active"] = False
            lease["quarantined_at"] = _datetime_to_text(now)
            raw["lease"] = lease
            raw["quarantine_reason"] = reason
            terminal = str(row["status"]) not in {"STARTING", "RUNNING"}
            operation_status = "MANUAL_REVIEW" if terminal else "NEEDS_RECONCILIATION"
            if terminal:
                connection.execute(
                    "UPDATE shadowbot_execution_attempts SET raw_output_json = ? WHERE execution_attempt_id = ?",
                    (_json_dump(raw), execution_attempt_id),
                )
            else:
                side_effect = str(row["side_effect_state"])
                attempt_status = "START_UNKNOWN" if side_effect == "NOT_STARTED" else "SIDE_EFFECT_UNKNOWN"
                connection.execute(
                    """
                    UPDATE shadowbot_execution_attempts
                    SET status = ?, side_effect_state = ?, ended_at = ?, raw_output_json = ?
                    WHERE execution_attempt_id = ?
                    """,
                    (
                        attempt_status,
                        "UNKNOWN" if attempt_status == "SIDE_EFFECT_UNKNOWN" else side_effect,
                        _datetime_to_text(now),
                        _json_dump(raw),
                        execution_attempt_id,
                    ),
                )
            connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = ?, lock_owner = '', updated_at = ? WHERE operation_id = ?
                """,
                (operation_status, _datetime_to_text(now), str(row["operation_id"])),
            )
            connection.commit()
            return True
        finally:
            connection.close()

    @staticmethod
    def _next_shadowbot_lease_version(connection: sqlite3.Connection, operation_id: str) -> int:
        rows = connection.execute(
            "SELECT raw_output_json FROM shadowbot_execution_attempts WHERE operation_id = ?",
            (operation_id,),
        ).fetchall()
        versions = []
        for row in rows:
            raw = _json_load(row["raw_output_json"])
            lease = raw.get("lease") if isinstance(raw.get("lease"), dict) else {}
            versions.append(int(lease.get("version") or 0))
        return max(versions, default=0) + 1

    def update_shadowbot_execution_attempt(
        self,
        execution_attempt_id: str,
        *,
        shadowbot_run_id: str | None = None,
        status: str | None = None,
        side_effect_state: str | None = None,
        instruction_hash: str | None = None,
        request_file_sha256: str | None = None,
        queue_request_path: str | None = None,
        ended_at: datetime | None = None,
        raw_output: dict[str, Any] | None = None,
    ) -> None:
        assignments: list[str] = []
        params: list[object] = []
        if shadowbot_run_id is not None:
            assignments.append("shadowbot_run_id = ?")
            params.append(shadowbot_run_id)
        if status is not None:
            assignments.append("status = ?")
            params.append(status)
        if side_effect_state is not None:
            assignments.append("side_effect_state = ?")
            params.append(side_effect_state)
        if instruction_hash is not None:
            assignments.append("instruction_hash = ?")
            params.append(instruction_hash)
        if request_file_sha256 is not None:
            assignments.append("request_file_sha256 = ?")
            params.append(request_file_sha256)
        if queue_request_path is not None:
            assignments.append("queue_request_path = ?")
            params.append(queue_request_path)
        if ended_at is not None:
            assignments.append("ended_at = ?")
            params.append(_datetime_to_text(ended_at))
        if raw_output is not None:
            assignments.append("raw_output_json = ?")
            params.append(_json_dump(raw_output))
        if not assignments:
            return
        params.append(execution_attempt_id)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                f"UPDATE shadowbot_execution_attempts SET {', '.join(assignments)} WHERE execution_attempt_id = ?",
                params,
            )

    def insert_shadowbot_side_effect_checkpoint(
        self,
        *,
        operation_id: str,
        execution_attempt_id: str,
        side_effect_state: str,
        checkpoint_at: datetime,
    ) -> ShadowBotSideEffectCheckpoint:
        with closing(self.connect()) as connection, connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM shadowbot_side_effect_checkpoints WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            version = int(row["next_version"])
            checkpoint = ShadowBotSideEffectCheckpoint(
                operation_id=operation_id,
                execution_attempt_id=execution_attempt_id,
                side_effect_state=side_effect_state,
                checkpoint_at=checkpoint_at,
                version=version,
            )
            connection.execute(
                """
                INSERT INTO shadowbot_side_effect_checkpoints(
                    operation_id, execution_attempt_id, side_effect_state, checkpoint_at, version
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.operation_id,
                    checkpoint.execution_attempt_id,
                    checkpoint.side_effect_state,
                    _datetime_to_text(checkpoint.checkpoint_at),
                    checkpoint.version,
                ),
            )
            connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET side_effect_state = ?
                WHERE execution_attempt_id = ?
                """,
                (side_effect_state, execution_attempt_id),
            )
        return checkpoint

    def latest_shadowbot_side_effect_checkpoint(self, operation_id: str) -> ShadowBotSideEffectCheckpoint | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM shadowbot_side_effect_checkpoints
                WHERE operation_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (operation_id,),
            ).fetchone()
        return _row_to_shadowbot_checkpoint(row) if row is not None else None

    def insert_retry_authorization(self, authorization: RetryAuthorization) -> int:
        row = _retry_authorization_to_row(authorization)
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO retry_authorizations(
                    retry_authorization_id, operation_id, source_execution_attempt_id,
                    authorization_type, authorized_by, evidence_type, evidence_hash,
                    approved_payload_hash, status, max_uses, consumed_by_execution_attempt_id,
                    expires_at, reason, created_at, consumed_at
                )
                VALUES(
                    :retry_authorization_id, :operation_id, :source_execution_attempt_id,
                    :authorization_type, :authorized_by, :evidence_type, :evidence_hash,
                    :approved_payload_hash, :status, :max_uses, :consumed_by_execution_attempt_id,
                    :expires_at, :reason, :created_at, :consumed_at
                )
                """,
                row,
            )
            return connection.total_changes - before

    def issue_retry_authorization(
        self,
        authorization: RetryAuthorization,
        *,
        allowed_operation_statuses: Iterable[str],
        retry_window_deadline: datetime,
        max_retry_window_seconds: int,
    ) -> bool:
        """Persist one authorization and expose RETRY_AUTHORIZED atomically."""
        allowed = tuple(dict.fromkeys(str(value) for value in allowed_operation_statuses))
        if not allowed:
            raise ValueError("allowed_operation_statuses must not be empty")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM shadowbot_operations WHERE operation_id = ?",
                (authorization.operation_id,),
            ).fetchone()
            source = connection.execute(
                "SELECT * FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?",
                (authorization.source_execution_attempt_id,),
            ).fetchone()
            reference_time = authorization.created_at or datetime.now(retry_window_deadline.tzinfo)
            source_raw = _json_load(source["raw_output_json"]) if source is not None else {}
            approval_expires_at = _text_to_datetime(source_raw.get("approval_expires_at"))
            if approval_expires_at is not None and approval_expires_at.tzinfo is None and reference_time.tzinfo is not None:
                approval_expires_at = approval_expires_at.replace(tzinfo=reference_time.tzinfo)
            operation_created_at = _text_to_datetime(operation["created_at"]) if operation is not None else None
            if operation_created_at is not None and operation_created_at.tzinfo is None and reference_time.tzinfo is not None:
                operation_created_at = operation_created_at.replace(tzinfo=reference_time.tzinfo)
            commit_rows = connection.execute(
                """
                SELECT started_at FROM shadowbot_execution_attempts
                WHERE operation_id = ? AND execution_mode = 'COMMIT'
                """,
                (authorization.operation_id,),
            ).fetchall()
            retry_origins = [
                value
                for value in (
                    operation_created_at,
                    *(_text_to_datetime(row["started_at"]) for row in commit_rows),
                )
                if value is not None
            ]
            retry_origins = [
                value.replace(tzinfo=reference_time.tzinfo)
                if value.tzinfo is None and reference_time.tzinfo is not None
                else value
                for value in retry_origins
            ]
            recomputed_deadline = (
                min(
                    min(retry_origins) + timedelta(seconds=max_retry_window_seconds),
                    approval_expires_at,
                )
                if retry_origins and approval_expires_at is not None and max_retry_window_seconds > 0
                else None
            )
            authorization_expires_at = authorization.expires_at
            if (
                operation is None
                or source is None
                or str(operation["status"]) not in allowed
                or str(operation["lock_owner"] or "")
                or str(operation["approved_payload_hash"]) != authorization.approved_payload_hash
                or str(source["operation_id"]) != authorization.operation_id
                or str(source["status"]) in {"STARTING", "RUNNING"}
                or recomputed_deadline is None
                or recomputed_deadline != retry_window_deadline
                or authorization_expires_at is None
                or authorization_expires_at > retry_window_deadline
                or reference_time > retry_window_deadline
            ):
                connection.rollback()
                return False
            active = connection.execute(
                """
                SELECT 1 FROM shadowbot_execution_attempts
                WHERE operation_id = ? AND execution_mode = 'COMMIT' AND status IN ('STARTING', 'RUNNING')
                LIMIT 1
                """,
                (authorization.operation_id,),
            ).fetchone()
            if active is not None:
                connection.rollback()
                return False
            source_raw["retry_window_deadline"] = _datetime_to_text(retry_window_deadline)
            source_raw["max_retry_window_seconds"] = max_retry_window_seconds
            source_raw["retry_window_authorization_id"] = authorization.retry_authorization_id
            connection.execute(
                "UPDATE shadowbot_execution_attempts SET raw_output_json = ? WHERE execution_attempt_id = ?",
                (_json_dump(source_raw), authorization.source_execution_attempt_id),
            )
            connection.execute(
                """
                INSERT INTO retry_authorizations(
                    retry_authorization_id, operation_id, source_execution_attempt_id,
                    authorization_type, authorized_by, evidence_type, evidence_hash,
                    approved_payload_hash, status, max_uses, consumed_by_execution_attempt_id,
                    expires_at, reason, created_at, consumed_at
                ) VALUES(
                    :retry_authorization_id, :operation_id, :source_execution_attempt_id,
                    :authorization_type, :authorized_by, :evidence_type, :evidence_hash,
                    :approved_payload_hash, :status, :max_uses, :consumed_by_execution_attempt_id,
                    :expires_at, :reason, :created_at, :consumed_at
                )
                """,
                _retry_authorization_to_row(authorization),
            )
            placeholders = ",".join("?" for _ in allowed)
            cursor = connection.execute(
                f"""
                UPDATE shadowbot_operations
                SET status = 'RETRY_AUTHORIZED', updated_at = ?
                WHERE operation_id = ? AND status IN ({placeholders}) AND lock_owner = ''
                """,
                (_datetime_to_text(datetime.now()), authorization.operation_id, *allowed),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
            return True
        except sqlite3.IntegrityError:
            connection.rollback()
            return False
        finally:
            connection.close()

    def consume_retry_authorization_and_create_attempt(
        self,
        retry_authorization_id: str,
        attempt: ShadowBotExecutionAttempt,
        *,
        owner_token: str,
        lease_expires_at: datetime,
        approved_payload_hash: str,
        consumed_at: datetime,
    ) -> ShadowBotExecutionAttempt | None:
        """Consume ACTIVE authorization and create the new attempt/lease in one transaction."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            authorization = connection.execute(
                "SELECT * FROM retry_authorizations WHERE retry_authorization_id = ?",
                (retry_authorization_id,),
            ).fetchone()
            operation = connection.execute(
                "SELECT * FROM shadowbot_operations WHERE operation_id = ?",
                (attempt.operation_id,),
            ).fetchone()
            if authorization is None or operation is None:
                connection.rollback()
                return None
            expires_at = _text_to_datetime(authorization["expires_at"])
            if expires_at is not None and expires_at.tzinfo is None and consumed_at.tzinfo is not None:
                expires_at = expires_at.replace(tzinfo=consumed_at.tzinfo)
            if (
                str(authorization["status"]) != "ACTIVE"
                or int(authorization["max_uses"]) != 1
                or str(authorization["operation_id"]) != attempt.operation_id
                or str(authorization["approved_payload_hash"]) != approved_payload_hash
                or expires_at is None
                or expires_at <= consumed_at
                or str(operation["status"]) != "RETRY_AUTHORIZED"
                or str(operation["approved_payload_hash"]) != approved_payload_hash
                or str(operation["lock_owner"] or "")
            ):
                connection.rollback()
                return None
            source = connection.execute(
                "SELECT * FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?",
                (str(authorization["source_execution_attempt_id"]),),
            ).fetchone()
            active = connection.execute(
                """
                SELECT 1 FROM shadowbot_execution_attempts
                WHERE operation_id = ? AND execution_mode = 'COMMIT' AND status IN ('STARTING', 'RUNNING')
                LIMIT 1
                """,
                (attempt.operation_id,),
            ).fetchone()
            source_raw = _json_load(source["raw_output_json"]) if source is not None else {}
            source_status = str(source["status"]) if source is not None else ""
            source_approval_expires_at = _text_to_datetime(source_raw.get("approval_expires_at"))
            if (
                source_approval_expires_at is not None
                and source_approval_expires_at.tzinfo is None
                and consumed_at.tzinfo is not None
            ):
                source_approval_expires_at = source_approval_expires_at.replace(tzinfo=consumed_at.tzinfo)
            retry_window_deadline = _text_to_datetime(source_raw.get("retry_window_deadline"))
            if (
                retry_window_deadline is not None
                and retry_window_deadline.tzinfo is None
                and consumed_at.tzinfo is not None
            ):
                retry_window_deadline = retry_window_deadline.replace(tzinfo=consumed_at.tzinfo)
            try:
                max_retry_window_seconds = int(source_raw.get("max_retry_window_seconds") or 0)
            except (TypeError, ValueError):
                max_retry_window_seconds = 0
            operation_created_at = _text_to_datetime(operation["created_at"])
            if operation_created_at is not None and operation_created_at.tzinfo is None and consumed_at.tzinfo is not None:
                operation_created_at = operation_created_at.replace(tzinfo=consumed_at.tzinfo)
            commit_rows = connection.execute(
                """
                SELECT started_at FROM shadowbot_execution_attempts
                WHERE operation_id = ? AND execution_mode = 'COMMIT'
                """,
                (attempt.operation_id,),
            ).fetchall()
            retry_origins = [
                value
                for value in (
                    operation_created_at,
                    *(_text_to_datetime(row["started_at"]) for row in commit_rows),
                )
                if value is not None
            ]
            retry_origins = [
                value.replace(tzinfo=consumed_at.tzinfo)
                if value.tzinfo is None and consumed_at.tzinfo is not None
                else value
                for value in retry_origins
            ]
            recomputed_retry_window_deadline = (
                min(
                    min(retry_origins) + timedelta(seconds=max_retry_window_seconds),
                    source_approval_expires_at,
                )
                if retry_origins and source_approval_expires_at is not None and max_retry_window_seconds > 0
                else None
            )
            retry_window_valid = bool(
                retry_window_deadline is not None
                and recomputed_retry_window_deadline == retry_window_deadline
                and consumed_at <= retry_window_deadline
            )
            source_approval_valid = bool(
                source_raw.get("approval_id")
                and str(source_raw.get("approved_payload_hash") or "") == approved_payload_hash
                and source_approval_expires_at is not None
                and source_approval_expires_at > consumed_at
            )
            frozen_manual_source = bool(
                source is not None
                and str(authorization["authorization_type"]) == "MANUAL"
                and source_raw.get("frozen_reason") == "DUPLICATE_ACTIVE_COMMIT_ATTEMPT"
                and (
                    (
                        str(authorization["evidence_type"]) == "PRE_PUBLISH_NOT_PUBLISHED"
                        and source_status == "START_UNKNOWN"
                        and str(source["side_effect_state"]) == "NOT_STARTED"
                    )
                    or (
                        str(authorization["evidence_type"]) == "NOT_APPLIED_RESULT"
                        and source_status in {"START_UNKNOWN", "SIDE_EFFECT_UNKNOWN"}
                    )
                )
            )
            normal_source = bool(
                source is not None
                and source_status in {"START_FAILED", "FAILED", "NOT_APPLIED"}
                and (
                    (source_status == "START_FAILED" and str(source["side_effect_state"]) == "NOT_STARTED")
                    or (
                        source_status in {"FAILED", "NOT_APPLIED"}
                        and str(source["side_effect_state"]) == "NOT_APPLIED"
                    )
                )
            )
            if (
                source is None
                or str(source["operation_id"]) != attempt.operation_id
                or str(source["execution_mode"]) != "COMMIT"
                or not source_approval_valid
                or not retry_window_valid
                or not (normal_source or frozen_manual_source)
                or active is not None
            ):
                connection.rollback()
                return None
            lease_version = self._next_shadowbot_lease_version(connection, attempt.operation_id)
            attempt.raw_output = {
                **attempt.raw_output,
                "retry_authorization_id": retry_authorization_id,
                "source_execution_attempt_id": str(authorization["source_execution_attempt_id"]),
                "operation_status_before_attempt": str(operation["status"]),
                "lease": {
                    "owner_token": owner_token,
                    "version": lease_version,
                    "expires_at": _datetime_to_text(lease_expires_at),
                    "active": True,
                },
            }
            connection.execute(
                """
                INSERT INTO shadowbot_execution_attempts(
                    execution_attempt_id, operation_id, execution_mode, shadowbot_run_id,
                    status, side_effect_state, started_at, instruction_hash,
                    request_file_sha256, queue_request_path, ended_at, raw_output_json
                ) VALUES(
                    :execution_attempt_id, :operation_id, :execution_mode, :shadowbot_run_id,
                    :status, :side_effect_state, :started_at, :instruction_hash,
                    :request_file_sha256, :queue_request_path, :ended_at, :raw_output_json
                )
                """,
                _shadowbot_attempt_to_row(attempt),
            )
            consumed = connection.execute(
                """
                UPDATE retry_authorizations
                SET status = 'CONSUMED', consumed_by_execution_attempt_id = ?, consumed_at = ?
                WHERE retry_authorization_id = ? AND status = 'ACTIVE' AND max_uses = 1
                  AND consumed_by_execution_attempt_id IS NULL AND expires_at > ?
                """,
                (
                    attempt.execution_attempt_id,
                    _datetime_to_text(consumed_at),
                    retry_authorization_id,
                    _datetime_to_text(consumed_at),
                ),
            )
            operation_updated = connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = 'RUNNING', lock_owner = ?, updated_at = ?
                WHERE operation_id = ? AND status = 'RETRY_AUTHORIZED' AND lock_owner = ''
                  AND approved_payload_hash = ?
                """,
                (
                    owner_token,
                    _datetime_to_text(consumed_at),
                    attempt.operation_id,
                    approved_payload_hash,
                ),
            )
            if consumed.rowcount != 1 or operation_updated.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
            return attempt
        except sqlite3.IntegrityError:
            connection.rollback()
            return None
        finally:
            connection.close()

    def get_retry_authorization(self, retry_authorization_id: str) -> RetryAuthorization | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM retry_authorizations WHERE retry_authorization_id = ?",
                (retry_authorization_id,),
            ).fetchone()
        return _row_to_retry_authorization(row) if row is not None else None

    def list_retry_authorizations(self, *, operation_id: str | None = None) -> list[RetryAuthorization]:
        query = "SELECT * FROM retry_authorizations"
        params: tuple[str, ...] = ()
        if operation_id:
            query += " WHERE operation_id = ?"
            params = (operation_id,)
        query += " ORDER BY created_at, retry_authorization_id"
        with closing(self.connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_retry_authorization(row) for row in rows]

    def insert_notification_logs(self, logs: Iterable[NotificationLog]) -> int:
        rows = [_notification_log_to_row(log) for log in logs]
        if not rows:
            return 0
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO notification_logs(
                    notification_id, related_task_id, related_review_task_id, recipient_type,
                    recipient, channel, sent_at, send_status, dedupe_key, message, error_message, created_at
                )
                VALUES(
                    :notification_id, :related_task_id, :related_review_task_id, :recipient_type,
                    :recipient, :channel, :sent_at, :send_status, :dedupe_key, :message, :error_message, :created_at
                )
                """,
                rows,
            )
            return connection.total_changes - before

    def list_notification_logs(
        self,
        *,
        related_review_task_id: str | None = None,
        send_status: str | None = None,
        channel: str | None = None,
    ) -> list[NotificationLog]:
        query = "SELECT * FROM notification_logs"
        clauses: list[str] = []
        params: list[str] = []
        if related_review_task_id:
            clauses.append("related_review_task_id = ?")
            params.append(related_review_task_id)
        if send_status:
            clauses.append("send_status = ?")
            params.append(send_status)
        if channel:
            clauses.append("channel = ?")
            params.append(channel)
        if clauses:
            query = f"{query} WHERE {' AND '.join(clauses)}"
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"{query} ORDER BY created_at DESC, notification_id ASC",
                params,
            ).fetchall()
        return [_row_to_notification_log(row) for row in rows]

    def get_notification_log(self, notification_id: str) -> NotificationLog | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM notification_logs WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
        return _row_to_notification_log(row) if row is not None else None

    def insert_script_run(self, script_run: ScriptRun) -> int:
        row = _script_run_to_row(script_run)
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO script_runs(
                    script_run_id, evaluator_id, evaluator_name, description, run_mode,
                    run_status, trade_date, started_at, finished_at, summary_json,
                    error_message, created_by
                )
                VALUES(
                    :script_run_id, :evaluator_id, :evaluator_name, :description, :run_mode,
                    :run_status, :trade_date, :started_at, :finished_at, :summary_json,
                    :error_message, :created_by
                )
                """,
                row,
            )
            return connection.total_changes - before

    def update_script_run(self, script_run: ScriptRun) -> None:
        row = _script_run_to_row(script_run)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE script_runs
                SET run_status = :run_status,
                    finished_at = :finished_at,
                    summary_json = :summary_json,
                    error_message = :error_message
                WHERE script_run_id = :script_run_id
                """,
                row,
            )

    def list_script_runs(self, *, limit: int | None = None) -> list[ScriptRun]:
        query = "SELECT * FROM script_runs ORDER BY started_at DESC, script_run_id ASC"
        params: list[object] = []
        if limit is not None:
            query = f"{query} LIMIT ?"
            params.append(limit)
        with closing(self.connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_script_run(row) for row in rows]

    def get_script_run(self, script_run_id: str) -> ScriptRun | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM script_runs WHERE script_run_id = ?",
                (script_run_id,),
            ).fetchone()
        return _row_to_script_run(row) if row is not None else None

    def insert_script_run_items(self, items: Iterable[ScriptRunItem]) -> int:
        rows = [_script_run_item_to_row(item) for item in items]
        if not rows:
            return 0
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO script_run_items(
                    item_id, script_run_id, proposal_type, dedupe_key, severity,
                    item_status, message, payload_json, decision_trace_json,
                    related_task_id, related_review_task_id, related_notification_id,
                    error_message, created_at
                )
                VALUES(
                    :item_id, :script_run_id, :proposal_type, :dedupe_key, :severity,
                    :item_status, :message, :payload_json, :decision_trace_json,
                    :related_task_id, :related_review_task_id, :related_notification_id,
                    :error_message, :created_at
                )
                """,
                rows,
            )
            return connection.total_changes - before

    def list_script_run_items(self, script_run_id: str) -> list[ScriptRunItem]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM script_run_items
                WHERE script_run_id = ?
                ORDER BY created_at ASC, item_id ASC
                """,
                (script_run_id,),
            ).fetchall()
        return [_row_to_script_run_item(row) for row in rows]


def _task_to_row(task: Task) -> dict[str, Any]:
    created_at = task.created_at
    updated_at = task.updated_at or created_at
    return {
        "task_id": task.task_id,
        "trade_date": _date_to_text(task.trade_date),
        "scope_type": task.scope_type,
        "scope_key": task.scope_key,
        "dedupe_key": task.dedupe_key,
        "internal_sku": task.internal_sku,
        "platform_name": task.platform_name,
        "action_type": task.action_type.value,
        "priority": task.priority,
        "task_status": task.task_status.value,
        "created_at": _datetime_to_text(created_at),
        "scheduled_at": _datetime_to_text(task.scheduled_at),
        "expires_at": _datetime_to_text(task.expires_at),
        "target_price": serialize_decimal(task.target_price),
        "target_status": task.target_status,
        "pricing_source": task.pricing_source.value if task.pricing_source else None,
        "decision_trace_json": _json_dump(task.decision_trace),
        "result_message": task.result_message,
        "required_by": _datetime_to_text(task.required_by),
        "updated_at": _datetime_to_text(updated_at),
    }


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        task_id=str(row["task_id"]),
        internal_sku=row["internal_sku"],
        platform_name=row["platform_name"],
        action_type=TaskActionType(str(row["action_type"])),
        priority=int(row["priority"]),
        task_status=TaskStatus(str(row["task_status"])),
        created_at=_text_to_datetime(row["created_at"]) or datetime.now(),
        target_price=Decimal(str(row["target_price"])) if row["target_price"] not in ("", None) else None,
        target_status=row["target_status"],
        pricing_source=PricingSource(str(row["pricing_source"])) if row["pricing_source"] not in ("", None) else None,
        decision_trace=_json_load(row["decision_trace_json"]),
        result_message=str(row["result_message"] or ""),
        required_by=_text_to_datetime(row["required_by"]),
        trade_date=_text_to_date(row["trade_date"]),
        scope_type=str(row["scope_type"]),
        scope_key=str(row["scope_key"]),
        dedupe_key=str(row["dedupe_key"] or ""),
        scheduled_at=_text_to_datetime(row["scheduled_at"]),
        expires_at=_text_to_datetime(row["expires_at"]),
        updated_at=_text_to_datetime(row["updated_at"]),
    )


def _review_task_to_row(review_task: ReviewTask) -> dict[str, Any]:
    created_at = review_task.created_at or datetime.now()
    updated_at = review_task.updated_at or created_at
    return {
        "review_task_id": review_task.review_task_id,
        "trade_date": _date_to_text(review_task.trade_date),
        "scope_type": review_task.scope_type,
        "scope_key": review_task.scope_key,
        "dedupe_key": review_task.dedupe_key,
        "source_task_id": review_task.source_task_id,
        "review_type": review_task.review_type,
        "review_status": review_task.review_status.value,
        "internal_sku": review_task.internal_sku,
        "platform_name": review_task.platform_name,
        "reason": review_task.reason,
        "review_payload_json": _json_dump(review_task.review_payload),
        "resolution_payload_json": _json_dump(review_task.resolution_payload),
        "required_by": _datetime_to_text(review_task.required_by),
        "created_at": _datetime_to_text(created_at),
        "updated_at": _datetime_to_text(updated_at),
        "resolved_by": review_task.resolved_by,
        "resolved_at": _datetime_to_text(review_task.resolved_at),
        "resolution_note": review_task.resolution_note,
    }


def _row_to_review_task(row: sqlite3.Row) -> ReviewTask:
    return ReviewTask(
        review_task_id=str(row["review_task_id"]),
        trade_date=_text_to_date(row["trade_date"]),
        scope_type=str(row["scope_type"]),
        scope_key=str(row["scope_key"]),
        dedupe_key=str(row["dedupe_key"] or ""),
        source_task_id=row["source_task_id"],
        review_type=str(row["review_type"]),
        review_status=ReviewTaskStatus(str(row["review_status"])),
        internal_sku=row["internal_sku"],
        platform_name=row["platform_name"],
        reason=str(row["reason"] or ""),
        review_payload=_json_load(row["review_payload_json"]),
        resolution_payload=_json_load(row["resolution_payload_json"]),
        required_by=_text_to_datetime(row["required_by"]),
        created_at=_text_to_datetime(row["created_at"]),
        updated_at=_text_to_datetime(row["updated_at"]),
        resolved_by=str(row["resolved_by"] or ""),
        resolved_at=_text_to_datetime(row["resolved_at"]),
        resolution_note=str(row["resolution_note"] or ""),
    )


def _review_token_to_row(review_token: ReviewToken) -> dict[str, Any]:
    created_at = review_token.created_at or datetime.now()
    return {
        "token_id": review_token.token_id,
        "review_task_id": review_token.review_task_id,
        "token_hash": review_token.token_hash,
        "token_subject": review_token.token_subject,
        "allowed_actions": _json_dump(review_token.allowed_actions),
        "expires_at": _datetime_to_text(review_token.expires_at),
        "used_at": _datetime_to_text(review_token.used_at),
        "revoked_at": _datetime_to_text(review_token.revoked_at),
        "created_at": _datetime_to_text(created_at),
        "created_by": review_token.created_by,
        "last_used_at": _datetime_to_text(review_token.last_used_at),
        "note": review_token.note,
    }


def _row_to_review_token(row: sqlite3.Row) -> ReviewToken:
    return ReviewToken(
        token_id=str(row["token_id"]),
        review_task_id=str(row["review_task_id"]),
        token_hash=str(row["token_hash"]),
        token_subject=str(row["token_subject"]),
        allowed_actions=_json_list_load(row["allowed_actions"]),
        expires_at=_text_to_datetime(row["expires_at"]) or datetime.now(),
        used_at=_text_to_datetime(row["used_at"]),
        revoked_at=_text_to_datetime(row["revoked_at"]),
        created_at=_text_to_datetime(row["created_at"]),
        created_by=str(row["created_by"]),
        last_used_at=_text_to_datetime(row["last_used_at"]),
        note=row["note"],
    )


def _execution_log_to_row(log: ExecutionLog) -> dict[str, Any]:
    return {
        "log_id": log.log_id,
        "task_id": log.task_id,
        "executor_name": log.executor_name,
        "start_time": _datetime_to_text(log.start_time),
        "end_time": _datetime_to_text(log.end_time),
        "success_flag": int(log.success_flag) if log.success_flag is not None else None,
        "error_code": log.error_code,
        "error_message": log.error_message,
        "raw_output": log.raw_output,
        "ai_model_version": log.ai_model_version,
        "ai_summary": log.ai_summary,
        "created_at": _datetime_to_text(log.created_at or datetime.now()),
    }


def _row_to_execution_log(row: sqlite3.Row) -> ExecutionLog:
    success_flag = row["success_flag"]
    return ExecutionLog(
        log_id=str(row["log_id"]),
        task_id=str(row["task_id"]),
        executor_name=str(row["executor_name"]),
        start_time=_text_to_datetime(row["start_time"]) or datetime.now(),
        end_time=_text_to_datetime(row["end_time"]),
        success_flag=bool(success_flag) if success_flag is not None else None,
        error_code=str(row["error_code"] or ""),
        error_message=str(row["error_message"] or ""),
        raw_output=str(row["raw_output"] or ""),
        ai_model_version=str(row["ai_model_version"] or ""),
        ai_summary=str(row["ai_summary"] or ""),
        created_at=_text_to_datetime(row["created_at"]),
    )


def _shadowbot_operation_to_row(operation: ShadowBotOperationLedger) -> dict[str, Any]:
    created_at = operation.created_at or datetime.now()
    updated_at = operation.updated_at or created_at
    return {
        "operation_id": operation.operation_id,
        "task_id": operation.task_id,
        "platform": operation.platform,
        "product_identity_json": _json_dump(operation.product_identity),
        "expected_old_price": serialize_decimal(operation.expected_old_price),
        "target_price": serialize_decimal(operation.target_price),
        "status": operation.status,
        "lock_owner": operation.lock_owner,
        "approved_payload_hash": operation.approved_payload_hash,
        "created_at": _datetime_to_text(created_at),
        "updated_at": _datetime_to_text(updated_at),
    }


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    columns = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _row_to_shadowbot_operation(row: sqlite3.Row) -> ShadowBotOperationLedger:
    return ShadowBotOperationLedger(
        operation_id=str(row["operation_id"]),
        task_id=str(row["task_id"]),
        platform=str(row["platform"]),
        product_identity=_json_load(row["product_identity_json"]),
        expected_old_price=Decimal(str(row["expected_old_price"])),
        target_price=Decimal(str(row["target_price"])),
        status=str(row["status"]),
        lock_owner=str(row["lock_owner"] or ""),
        approved_payload_hash=str(row["approved_payload_hash"] or ""),
        created_at=_text_to_datetime(row["created_at"]),
        updated_at=_text_to_datetime(row["updated_at"]),
    )


def _shadowbot_attempt_to_row(attempt: ShadowBotExecutionAttempt) -> dict[str, Any]:
    return {
        "execution_attempt_id": attempt.execution_attempt_id,
        "operation_id": attempt.operation_id,
        "execution_mode": attempt.execution_mode,
        "shadowbot_run_id": attempt.shadowbot_run_id,
        "status": attempt.status,
        "side_effect_state": attempt.side_effect_state,
        "started_at": _datetime_to_text(attempt.started_at),
        "instruction_hash": attempt.instruction_hash,
        "request_file_sha256": attempt.request_file_sha256,
        "queue_request_path": attempt.queue_request_path,
        "ended_at": _datetime_to_text(attempt.ended_at),
        "raw_output_json": _json_dump(attempt.raw_output),
    }


def _row_to_shadowbot_attempt(row: sqlite3.Row) -> ShadowBotExecutionAttempt:
    return ShadowBotExecutionAttempt(
        execution_attempt_id=str(row["execution_attempt_id"]),
        operation_id=str(row["operation_id"]),
        execution_mode=str(row["execution_mode"]),
        shadowbot_run_id=str(row["shadowbot_run_id"] or ""),
        status=str(row["status"]),
        side_effect_state=str(row["side_effect_state"]),
        started_at=_text_to_datetime(row["started_at"]) or datetime.now(),
        instruction_hash=str(row["instruction_hash"] or ""),
        request_file_sha256=str(row["request_file_sha256"] or ""),
        queue_request_path=str(row["queue_request_path"] or ""),
        ended_at=_text_to_datetime(row["ended_at"]),
        raw_output=_json_load(row["raw_output_json"]),
    )


def _row_to_shadowbot_checkpoint(row: sqlite3.Row) -> ShadowBotSideEffectCheckpoint:
    return ShadowBotSideEffectCheckpoint(
        operation_id=str(row["operation_id"]),
        execution_attempt_id=str(row["execution_attempt_id"]),
        side_effect_state=str(row["side_effect_state"]),
        checkpoint_at=_text_to_datetime(row["checkpoint_at"]) or datetime.now(),
        version=int(row["version"]),
    )


def _retry_authorization_to_row(authorization: RetryAuthorization) -> dict[str, Any]:
    created_at = authorization.created_at or datetime.now()
    expires_at = authorization.expires_at or created_at
    return {
        "retry_authorization_id": authorization.retry_authorization_id,
        "operation_id": authorization.operation_id,
        "source_execution_attempt_id": authorization.source_execution_attempt_id,
        "authorization_type": authorization.authorization_type,
        "authorized_by": authorization.authorized_by,
        "evidence_type": authorization.evidence_type,
        "evidence_hash": authorization.evidence_hash,
        "approved_payload_hash": authorization.approved_payload_hash,
        "status": authorization.status,
        "max_uses": authorization.max_uses,
        "consumed_by_execution_attempt_id": authorization.consumed_by_execution_attempt_id,
        "expires_at": _datetime_to_text(expires_at),
        "reason": authorization.reason,
        "created_at": _datetime_to_text(created_at),
        "consumed_at": _datetime_to_text(authorization.consumed_at),
    }


def _row_to_retry_authorization(row: sqlite3.Row) -> RetryAuthorization:
    return RetryAuthorization(
        retry_authorization_id=str(row["retry_authorization_id"]),
        operation_id=str(row["operation_id"]),
        source_execution_attempt_id=str(row["source_execution_attempt_id"]),
        authorization_type=str(row["authorization_type"]),
        authorized_by=str(row["authorized_by"]),
        evidence_type=str(row["evidence_type"]),
        evidence_hash=str(row["evidence_hash"]),
        approved_payload_hash=str(row["approved_payload_hash"]),
        status=str(row["status"]),
        max_uses=int(row["max_uses"]),
        consumed_by_execution_attempt_id=(
            str(row["consumed_by_execution_attempt_id"])
            if row["consumed_by_execution_attempt_id"] is not None
            else None
        ),
        expires_at=_text_to_datetime(row["expires_at"]),
        reason=str(row["reason"] or ""),
        created_at=_text_to_datetime(row["created_at"]),
        consumed_at=_text_to_datetime(row["consumed_at"]),
    )


def _notification_log_to_row(log: NotificationLog) -> dict[str, Any]:
    return {
        "notification_id": log.notification_id,
        "related_task_id": log.related_task_id,
        "related_review_task_id": log.related_review_task_id,
        "recipient_type": log.recipient_type,
        "recipient": log.recipient,
        "channel": log.channel,
        "sent_at": _datetime_to_text(log.sent_at),
        "send_status": log.send_status,
        "dedupe_key": log.dedupe_key,
        "message": log.message,
        "error_message": log.error_message,
        "created_at": _datetime_to_text(log.created_at or datetime.now()),
    }


def _row_to_notification_log(row: sqlite3.Row) -> NotificationLog:
    return NotificationLog(
        notification_id=str(row["notification_id"]),
        related_task_id=row["related_task_id"],
        related_review_task_id=row["related_review_task_id"],
        recipient_type=str(row["recipient_type"]),
        recipient=str(row["recipient"]),
        channel=str(row["channel"]),
        sent_at=_text_to_datetime(row["sent_at"]),
        send_status=str(row["send_status"]),
        dedupe_key=str(row["dedupe_key"] or ""),
        message=str(row["message"] or ""),
        error_message=str(row["error_message"] or ""),
        created_at=_text_to_datetime(row["created_at"]),
    )


def _script_run_to_row(script_run: ScriptRun) -> dict[str, Any]:
    return {
        "script_run_id": script_run.script_run_id,
        "evaluator_id": script_run.evaluator_id,
        "evaluator_name": script_run.evaluator_name,
        "description": script_run.description,
        "run_mode": script_run.run_mode,
        "run_status": script_run.run_status,
        "trade_date": _date_to_text(script_run.trade_date),
        "started_at": _datetime_to_text(script_run.started_at),
        "finished_at": _datetime_to_text(script_run.finished_at),
        "summary_json": _json_dump(script_run.summary),
        "error_message": script_run.error_message,
        "created_by": script_run.created_by,
    }


def _row_to_script_run(row: sqlite3.Row) -> ScriptRun:
    return ScriptRun(
        script_run_id=str(row["script_run_id"]),
        evaluator_id=str(row["evaluator_id"]),
        evaluator_name=str(row["evaluator_name"]),
        description=str(row["description"] or ""),
        run_mode=str(row["run_mode"]),
        run_status=str(row["run_status"]),
        trade_date=_text_to_date(row["trade_date"]),
        started_at=_text_to_datetime(row["started_at"]) or datetime.now(),
        finished_at=_text_to_datetime(row["finished_at"]),
        summary=_json_load(row["summary_json"]),
        error_message=str(row["error_message"] or ""),
        created_by=str(row["created_by"] or "system"),
    )


def _script_run_item_to_row(item: ScriptRunItem) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "script_run_id": item.script_run_id,
        "proposal_type": item.proposal_type,
        "dedupe_key": item.dedupe_key,
        "severity": item.severity,
        "item_status": item.item_status,
        "message": item.message,
        "payload_json": _json_dump(item.payload),
        "decision_trace_json": _json_dump(item.decision_trace),
        "related_task_id": item.related_task_id,
        "related_review_task_id": item.related_review_task_id,
        "related_notification_id": item.related_notification_id,
        "error_message": item.error_message,
        "created_at": _datetime_to_text(item.created_at or datetime.now()),
    }


def _row_to_script_run_item(row: sqlite3.Row) -> ScriptRunItem:
    return ScriptRunItem(
        item_id=str(row["item_id"]),
        script_run_id=str(row["script_run_id"]),
        proposal_type=str(row["proposal_type"]),
        dedupe_key=str(row["dedupe_key"]),
        severity=str(row["severity"]),
        item_status=str(row["item_status"]),
        message=str(row["message"] or ""),
        payload=_json_load(row["payload_json"]),
        decision_trace=_json_load(row["decision_trace_json"]),
        related_task_id=row["related_task_id"],
        related_review_task_id=row["related_review_task_id"],
        related_notification_id=row["related_notification_id"],
        error_message=str(row["error_message"] or ""),
        created_at=_text_to_datetime(row["created_at"]),
    )


def _row_to_status_history(row: sqlite3.Row) -> TaskStatusHistory:
    return TaskStatusHistory(
        history_id=str(row["history_id"]),
        task_id=str(row["task_id"]),
        from_status=TaskStatus(str(row["from_status"])) if row["from_status"] not in ("", None) else None,
        to_status=TaskStatus(str(row["to_status"])),
        changed_by=str(row["changed_by"]),
        changed_at=_text_to_datetime(row["changed_at"]) or datetime.now(),
        reason=str(row["reason"] or ""),
        metadata=_json_load(row["metadata_json"]),
    )


def _is_sqlite_concurrency_error(error: sqlite3.OperationalError) -> bool:
    """Classify SQLite busy/locked errors without inspecting localized text."""

    error_code = getattr(error, "sqlite_errorcode", None)
    error_name = getattr(error, "sqlite_errorname", "")
    if error_code in SQLITE_CONCURRENCY_ERROR_CODES:
        return True
    return bool(
        isinstance(error_name, str)
        and (error_name.startswith("SQLITE_BUSY") or error_name.startswith("SQLITE_LOCKED"))
    )


def _atomic_source_task_status(
    source_row: sqlite3.Row | None,
    status: ReviewTaskStatus,
) -> TaskStatus | None:
    if source_row is None:
        return None
    current_status = TaskStatus(str(source_row["task_status"]))
    if current_status == TaskStatus.MANUAL_REVIEW:
        if status in {ReviewTaskStatus.APPROVED, ReviewTaskStatus.ADJUSTED}:
            return TaskStatus.PENDING
        if status == ReviewTaskStatus.CANCELLED:
            return TaskStatus.CANCELLED
        return TaskStatus.SKIPPED
    if current_status == TaskStatus.PENDING:
        action_type = TaskActionType(str(source_row["action_type"]))
        if action_type in MANUAL_REVIEW_SOURCE_ACTIONS:
            if status == ReviewTaskStatus.CANCELLED:
                return TaskStatus.CANCELLED
            if status == ReviewTaskStatus.ADJUSTED:
                return TaskStatus.PENDING
            return TaskStatus.SKIPPED
    return None


def _resolution_payload_summary(payload: dict[str, object]) -> dict[str, object]:
    if not payload:
        return {}
    summary: dict[str, object] = {"keys": sorted(payload.keys())}
    if "reviewer_code" in payload and payload.get("reviewer_code"):
        summary["reviewer_code_present"] = True
    if "adjustment" in payload:
        summary["adjustment"] = payload.get("adjustment")
    return summary


def _date_to_text(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _text_to_date(value: str | None) -> date | None:
    if value in ("", None):
        return None
    return date.fromisoformat(str(value))


def _text_to_datetime(value: str | None) -> datetime | None:
    if value in ("", None):
        return None
    return datetime.fromisoformat(str(value))


def _json_dump(value: Any) -> str:
    return json.dumps({} if value is None else value, ensure_ascii=False, sort_keys=True)


def _json_load(value: str | None) -> dict[str, Any]:
    if value in ("", None):
        return {}
    loaded = json.loads(str(value))
    return loaded if isinstance(loaded, dict) else {}


def _json_list_load(value: str | None) -> list[str]:
    if value in ("", None):
        return []
    loaded = json.loads(str(value))
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded]

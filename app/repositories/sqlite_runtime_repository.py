from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from app.enums import PricingSource, ReviewTaskStatus, TaskActionType, TaskStatus
from app.models import ExecutionLog, NotificationLog, ReviewTask, Task, TaskStatusHistory
from app.utils import serialize_decimal


SCHEMA_VERSION = 1
TERMINAL_TASK_STATUSES = ("success", "skipped", "cancelled", "expired")


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
            connection.execute(
                """
                INSERT OR IGNORE INTO runtime_schema_migrations(schema_version, applied_at, note)
                VALUES (?, ?, ?)
                """,
                (SCHEMA_VERSION, _datetime_to_text(datetime.now()), "initial runtime schema"),
            )

    def schema_versions(self) -> list[int]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT schema_version FROM runtime_schema_migrations ORDER BY schema_version"
            ).fetchall()
        return [int(row["schema_version"]) for row in rows]

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

    def list_tasks(self, *, status: TaskStatus | None = None, action_type: TaskActionType | None = None) -> list[Task]:
        query = "SELECT * FROM tasks"
        clauses: list[str] = []
        params: list[str] = []
        if status is not None:
            clauses.append("task_status = ?")
            params.append(status.value)
        if action_type is not None:
            clauses.append("action_type = ?")
            params.append(action_type.value)
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

    def list_review_tasks(self, *, status: ReviewTaskStatus | None = None) -> list[ReviewTask]:
        query = "SELECT * FROM review_tasks"
        params: list[str] = []
        if status is not None:
            query = f"{query} WHERE review_status = ?"
            params.append(status.value)
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

    def list_notification_logs(self) -> list[NotificationLog]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM notification_logs ORDER BY created_at DESC, notification_id ASC"
            ).fetchall()
        return [_row_to_notification_log(row) for row in rows]


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


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _json_load(value: str | None) -> dict[str, Any]:
    if value in ("", None):
        return {}
    loaded = json.loads(str(value))
    return loaded if isinstance(loaded, dict) else {}

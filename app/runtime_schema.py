"""Authoritative runtime SQLite schema metadata and health checks.

This module intentionally contains the only latest-runtime-schema version
constant used by the application.  Repository migrations and operational
health checks both consume the metadata here so a migration row cannot make a
database look healthy when its physical structure is incomplete.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping

from app.services.shadowbot_price_batch import BatchItemStatus, BatchStatus, WRITE_LOCK_STATES


LATEST_RUNTIME_SCHEMA_VERSION = 7
RUNTIME_SCHEMA_VERSIONS = tuple(range(1, LATEST_RUNTIME_SCHEMA_VERSION + 1))

REQUIRED_RUNTIME_TABLES = frozenset(
    {
        "runtime_schema_migrations",
        "tasks",
        "review_tasks",
        "review_tokens",
        "execution_logs",
        "notification_logs",
        "task_status_history",
        "script_runs",
        "script_run_items",
        "shadowbot_operations",
        "shadowbot_execution_attempts",
        "shadowbot_side_effect_checkpoints",
        "retry_authorizations",
        "notification_outbox",
        "notification_delivery_attempts",
        "shadowbot_batches",
        "shadowbot_batch_items",
        "shadowbot_batch_control_events",
    }
)

V5_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "shadowbot_execution_attempts": (
        "instruction_hash",
        "request_file_sha256",
        "queue_request_path",
    ),
    "retry_authorizations": (
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
    ),
}

V6_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "notification_outbox": (
        "notification_id",
        "notification_key",
        "notification_type",
        "related_task_id",
        "related_review_task_id",
        "recipient_type",
        "recipient_ref",
        "channel",
        "priority",
        "payload_json",
        "status",
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "deadline_at",
        "lease_owner_token",
        "lease_version",
        "lease_expires_at",
        "sent_at",
        "provider_message_id",
        "last_error_code",
        "last_error_message",
        "created_at",
        "updated_at",
    ),
    "notification_delivery_attempts": (
        "delivery_attempt_id",
        "notification_id",
        "attempt_no",
        "status",
        "lease_owner_token",
        "lease_version",
        "request_fingerprint",
        "started_at",
        "completed_at",
        "provider_status_code",
        "provider_message_id",
        "response_fingerprint",
        "error_code",
        "error_message",
    ),
}

V7_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "shadowbot_operations": (
        "write_identity_key",
        "page_identity_key",
    ),
    "shadowbot_batches": (
        "batch_id",
        "contract_version",
        "platform",
        "batch_type",
        "execution_mode",
        "identity_normalization_version",
        "normalized_request_digest",
        "stop_policy",
        "source_read_batch_id",
        "source_snapshot_sha256",
        "source_page_context_sha256",
        "source_observed_at",
        "source_snapshot_max_age_seconds",
        "status",
        "current_item_id",
        "pending_count",
        "ready_count",
        "running_count",
        "processed_count",
        "previewed_count",
        "verified_count",
        "failed_count",
        "skipped_count",
        "cancelled_count",
        "needs_reconciliation_count",
        "reconciled_item_count",
        "paused_reason",
        "error_code",
        "created_by",
        "capture_evidence",
        "created_at",
        "started_at",
        "completed_at",
        "updated_at",
    ),
    "shadowbot_batch_items": (
        "batch_id",
        "item_id",
        "ordinal",
        "source_item_id",
        "source_read_batch_id",
        "source_snapshot_sha256",
        "source_page_context_sha256",
        "task_id",
        "review_task_id",
        "operation_id",
        "approved_payload_hash",
        "page_identity_key",
        "write_identity_key",
        "external_platform_sku",
        "expected_product_name",
        "expected_grade",
        "approved_expected_old_price",
        "target_price",
        "status",
        "current_execution_attempt_id",
        "current_run_id",
        "fresh_read_attempt_id",
        "fresh_read_result_sha256",
        "fresh_old_price",
        "post_commit_price",
        "reconcile_attempt_id",
        "reconciliation_outcome",
        "reconciled_at",
        "error_code",
        "error_message",
        "result_id",
        "result_hash",
        "started_at",
        "completed_at",
        "updated_at",
    ),
    "shadowbot_batch_control_events": (
        "event_id",
        "batch_id",
        "action",
        "actor",
        "reason",
        "previous_status",
        "resulting_status",
        "applied",
        "created_at",
    ),
}

PRICE_BATCH_INDEX_SPECS: Mapping[str, tuple[tuple[str, ...], bool]] = {
    "ix_shadowbot_batches_status": (("status", "created_at"), False),
    "ix_shadowbot_batches_digest": (("normalized_request_digest",), False),
    "ux_shadowbot_batch_items_ordinal": (("batch_id", "ordinal"), True),
    "ux_shadowbot_batch_items_operation_id": (("operation_id",), True),
    "ux_shadowbot_batch_items_reconcile_attempt_id": (("reconcile_attempt_id",), True),
    "ix_shadowbot_batch_items_status": (("batch_id", "status", "ordinal"), False),
    "ix_shadowbot_batch_control_events_batch": (("batch_id", "created_at"), False),
    "ix_shadowbot_operations_write_identity_status": (("write_identity_key", "status"), False),
    "ix_shadowbot_operations_page_identity_status": (("page_identity_key", "status"), False),
    "ux_shadowbot_operations_active_write_identity": (("write_identity_key",), True),
    "ux_shadowbot_operations_active_page_identity": (("page_identity_key",), True),
}
PRICE_BATCH_INDEXES = frozenset(PRICE_BATCH_INDEX_SPECS)

NOTIFICATION_OUTBOX_STATUS_VALUES = frozenset(
    {
        "PENDING",
        "LEASED",
        "SENDING",
        "RETRY_WAIT",
        "SENT",
        "UNKNOWN_DELIVERY",
        "FAILED",
        "EXPIRED",
        "CANCELLED",
    }
)
DELIVERY_ATTEMPT_STATUS_VALUES = frozenset(
    {"STARTED", "ACKNOWLEDGED", "TEMP_FAILED", "PERM_FAILED", "UNKNOWN"}
)

RETRY_AUTHORIZATION_STATUS_VALUES = frozenset({"ACTIVE", "CONSUMED", "EXPIRED", "REVOKED"})
RETRY_AUTHORIZATION_INDEX_SPECS: Mapping[str, tuple[tuple[str, ...], bool]] = {
    "ix_retry_authorizations_operation_id": (("operation_id",), False),
    "ix_retry_authorizations_status": (("status",), False),
    "ix_retry_authorizations_expires_at": (("expires_at",), False),
    "ux_retry_authorizations_evidence_hash": (("evidence_hash",), True),
    "ux_retry_authorizations_consumed_by_execution_attempt_id": (
        ("consumed_by_execution_attempt_id",),
        True,
    ),
}
RETRY_AUTHORIZATION_INDEXES = frozenset(RETRY_AUTHORIZATION_INDEX_SPECS)

NOTIFICATION_OUTBOX_INDEX_SPECS: Mapping[str, tuple[tuple[str, ...], bool]] = {
    "ux_notification_outbox_key": (("notification_key",), True),
    "ix_notification_outbox_claim": (
        ("status", "priority", "next_attempt_at", "deadline_at", "created_at"),
        False,
    ),
    "ix_notification_outbox_lease_expires_at": (("lease_expires_at",), False),
    "ix_notification_delivery_attempts_notification_id": (("notification_id",), False),
}
NOTIFICATION_OUTBOX_INDEXES = frozenset(NOTIFICATION_OUTBOX_INDEX_SPECS)


@dataclass(frozen=True, slots=True)
class RuntimeSchemaHealth:
    """Structured result returned by runtime schema health checks."""

    ok: bool
    actual_version: int | None
    applied_versions: tuple[int, ...]
    version_matches: bool
    missing_tables: tuple[str, ...]
    missing_columns: Mapping[str, tuple[str, ...]]
    missing_indexes: tuple[str, ...]
    constraint_errors: tuple[str, ...]
    error: str | None = None

    def __bool__(self) -> bool:
        return self.ok

    @property
    def summary(self) -> str:
        if self.ok:
            return f"runtime schema v{LATEST_RUNTIME_SCHEMA_VERSION} healthy"
        parts: list[str] = []
        if not self.version_matches:
            parts.append(
                f"version expected {LATEST_RUNTIME_SCHEMA_VERSION}, actual {self.actual_version or 0}"
            )
        if self.missing_tables:
            parts.append("missing tables: " + ", ".join(self.missing_tables))
        if self.missing_columns:
            parts.append(
                "missing columns: "
                + "; ".join(f"{table}({', '.join(columns)})" for table, columns in self.missing_columns.items())
            )
        if self.missing_indexes:
            parts.append("missing indexes: " + ", ".join(self.missing_indexes))
        if self.constraint_errors:
            parts.append("constraints: " + ", ".join(self.constraint_errors))
        if self.error:
            parts.append(self.error)
        return "; ".join(parts) or "runtime schema unhealthy"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "actual_version": self.actual_version,
            "applied_versions": list(self.applied_versions),
            "version_matches": self.version_matches,
            "missing_tables": list(self.missing_tables),
            "missing_columns": {table: list(columns) for table, columns in self.missing_columns.items()},
            "missing_indexes": list(self.missing_indexes),
            "constraint_errors": list(self.constraint_errors),
            "error": self.error,
            "summary": self.summary,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.as_dict()


def inspect_runtime_schema(connection: sqlite3.Connection) -> RuntimeSchemaHealth:
    """Inspect a SQLite connection without mutating it.

    The check is deliberately exact: a database with a migration row for v7
    but a missing table, column, index, or constraint is unhealthy.
    """

    try:
        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        tables = {str(row[0]) for row in table_rows}
        missing_tables = tuple(sorted(REQUIRED_RUNTIME_TABLES - tables))

        applied_versions: tuple[int, ...] = ()
        if "runtime_schema_migrations" in tables:
            rows = connection.execute(
                "SELECT schema_version FROM runtime_schema_migrations ORDER BY schema_version"
            ).fetchall()
            applied_versions = tuple(int(row[0]) for row in rows)
        actual_version = max(applied_versions) if applied_versions else None
        version_matches = applied_versions == RUNTIME_SCHEMA_VERSIONS and actual_version == LATEST_RUNTIME_SCHEMA_VERSION

        missing_columns: dict[str, tuple[str, ...]] = {}
        for table, required in {**V5_REQUIRED_COLUMNS, **V6_REQUIRED_COLUMNS, **V7_REQUIRED_COLUMNS}.items():
            if table not in tables:
                continue
            columns = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            absent = tuple(column for column in required if column not in columns)
            if absent:
                missing_columns[table] = absent

        constraint_errors: list[str] = []
        missing_indexes: tuple[str, ...]
        missing_index_names: set[str] = set()
        if "retry_authorizations" in tables and "retry_authorizations" not in missing_tables:
            missing_index_names.update(_check_retry_authorization_constraints(connection, constraint_errors))
        else:
            missing_index_names.update(RETRY_AUTHORIZATION_INDEXES)
        if (
            "notification_outbox" in tables
            and "notification_delivery_attempts" in tables
            and "notification_outbox" not in missing_tables
            and "notification_delivery_attempts" not in missing_tables
        ):
            missing_index_names.update(_check_notification_outbox_constraints(connection, constraint_errors))
        else:
            missing_index_names.update(NOTIFICATION_OUTBOX_INDEXES)
        if {
            "shadowbot_operations",
            "shadowbot_batches",
            "shadowbot_batch_items",
        }.issubset(tables):
            missing_index_names.update(_check_price_batch_constraints(connection, constraint_errors))
        else:
            missing_index_names.update(PRICE_BATCH_INDEXES)
        missing_indexes = tuple(sorted(missing_index_names))

        ok = not (
            missing_tables
            or missing_columns
            or missing_indexes
            or constraint_errors
            or not version_matches
        )
        return RuntimeSchemaHealth(
            ok=ok,
            actual_version=actual_version,
            applied_versions=applied_versions,
            version_matches=version_matches,
            missing_tables=missing_tables,
            missing_columns=missing_columns,
            missing_indexes=missing_indexes,
            constraint_errors=tuple(constraint_errors),
        )
    except sqlite3.Error as exc:
        return RuntimeSchemaHealth(
            ok=False,
            actual_version=None,
            applied_versions=(),
            version_matches=False,
            missing_tables=tuple(sorted(REQUIRED_RUNTIME_TABLES)),
            missing_columns={},
            missing_indexes=tuple(
                sorted(RETRY_AUTHORIZATION_INDEXES | NOTIFICATION_OUTBOX_INDEXES | PRICE_BATCH_INDEXES)
            ),
            constraint_errors=(),
            error=f"sqlite error: {type(exc).__name__}",
        )


def check_runtime_schema(connection: sqlite3.Connection) -> RuntimeSchemaHealth:
    """Compatibility alias for callers that name the operation a check."""

    return inspect_runtime_schema(connection)


def runtime_schema_health(connection: sqlite3.Connection) -> RuntimeSchemaHealth:
    """Alias for integrations that use health-check terminology."""

    return inspect_runtime_schema(connection)


def assert_runtime_schema(connection: sqlite3.Connection) -> RuntimeSchemaHealth:
    """Raise a diagnostic error unless the connection has the exact v7 shape."""

    result = inspect_runtime_schema(connection)
    if not result.ok:
        raise RuntimeError(result.summary)
    return result


def _check_retry_authorization_constraints(
    connection: sqlite3.Connection,
    errors: list[str],
) -> tuple[str, ...]:
    table_info = connection.execute("PRAGMA table_info(retry_authorizations)").fetchall()
    by_name = {str(row[1]): row for row in table_info}
    primary_key = by_name.get("retry_authorization_id")
    if primary_key is None or int(primary_key[5]) != 1:
        errors.append("retry_authorizations.retry_authorization_id is not the primary key")

    foreign_keys = connection.execute("PRAGMA foreign_key_list(retry_authorizations)").fetchall()
    # SQLite PRAGMA foreign_key_list columns are (id, seq, table, from, to,
    # on_update, on_delete, match).  The referenced column is part of the
    # schema contract: accepting only the target table would let a malformed
    # v5 database point at an unrelated column and still report healthy.
    foreign_key_specs = {
        (str(row[3]), str(row[2]), str(row[4]))
        for row in foreign_keys
    }
    for column, target_table, target_column in (
        ("operation_id", "shadowbot_operations", "operation_id"),
        (
            "source_execution_attempt_id",
            "shadowbot_execution_attempts",
            "execution_attempt_id",
        ),
    ):
        if (column, target_table, target_column) not in foreign_key_specs:
            errors.append(
                f"missing foreign key {column} -> {target_table}({target_column})"
            )

    sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'retry_authorizations'"
    ).fetchone()
    table_sql = str(sql_row[0] or "") if sql_row else ""
    if not re.search(r"\bCHECK\s*\(\s*max_uses\s*=\s*1\s*\)", table_sql, re.IGNORECASE):
        errors.append("retry_authorizations.max_uses lacks CHECK (max_uses = 1)")

    status_match = re.search(
        r"\bstatus\b\s+[^,]*?\bCHECK\s*\(\s*status\s+IN\s*\((?P<values>[^)]*)\)\)",
        table_sql,
        re.IGNORECASE | re.DOTALL,
    )
    status_values = tuple(
        value.replace("''", "'").upper()
        for value in re.findall(r"'((?:''|[^'])*)'", status_match.group("values"))
    ) if status_match else ()
    expected_status_values = tuple(sorted(RETRY_AUTHORIZATION_STATUS_VALUES))
    if (
        not status_match
        or len(status_values) != len(expected_status_values)
        or set(status_values) != RETRY_AUTHORIZATION_STATUS_VALUES
    ):
        errors.append(
            "retry_authorizations.status CHECK must allow exactly "
            + ", ".join(expected_status_values)
        )

    index_rows = {
        str(row[1]): row
        for row in connection.execute("PRAGMA index_list('retry_authorizations')").fetchall()
    }
    missing_indexes: list[str] = []
    for index_name, (expected_columns, expected_unique) in RETRY_AUTHORIZATION_INDEX_SPECS.items():
        row = index_rows.get(index_name)
        if row is None:
            missing_indexes.append(index_name)
            continue
        actual_unique = int(row[2]) == 1
        if actual_unique != expected_unique:
            errors.append(
                f"{index_name} unique={actual_unique}, expected {expected_unique}"
            )
        actual_columns = tuple(
            str(index_row[2])
            for index_row in connection.execute(f"PRAGMA index_info('{index_name}')").fetchall()
        )
        if actual_columns != expected_columns:
            errors.append(
                f"{index_name} columns={actual_columns}, expected {expected_columns}"
            )
    return tuple(sorted(missing_indexes))


def _check_notification_outbox_constraints(
    connection: sqlite3.Connection,
    errors: list[str],
) -> tuple[str, ...]:
    """Validate v6 keys, foreign keys, status/numeric checks, and indexes."""

    outbox_info = connection.execute("PRAGMA table_info(notification_outbox)").fetchall()
    outbox_columns = {str(row[1]): row for row in outbox_info}
    if not outbox_columns.get("notification_id") or int(outbox_columns["notification_id"][5]) != 1:
        errors.append("notification_outbox.notification_id is not the primary key")

    attempt_info = connection.execute("PRAGMA table_info(notification_delivery_attempts)").fetchall()
    attempt_columns = {str(row[1]): row for row in attempt_info}
    if not attempt_columns.get("delivery_attempt_id") or int(attempt_columns["delivery_attempt_id"][5]) != 1:
        errors.append("notification_delivery_attempts.delivery_attempt_id is not the primary key")

    foreign_key_specs = {
        (str(row[3]), str(row[2]), str(row[4]))
        for row in connection.execute("PRAGMA foreign_key_list(notification_outbox)").fetchall()
    }
    for column, target in (
        ("related_task_id", ("tasks", "task_id")),
        ("related_review_task_id", ("review_tasks", "review_task_id")),
    ):
        if (column, *target) not in foreign_key_specs:
            errors.append(f"missing foreign key {column} -> {target[0]}({target[1]})")
    attempt_foreign_keys = {
        (str(row[3]), str(row[2]), str(row[4]))
        for row in connection.execute("PRAGMA foreign_key_list(notification_delivery_attempts)").fetchall()
    }
    if ("notification_id", "notification_outbox", "notification_id") not in attempt_foreign_keys:
        errors.append(
            "missing foreign key notification_id -> notification_outbox(notification_id)"
        )

    table_sql_rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
        "AND name IN ('notification_outbox', 'notification_delivery_attempts')"
    ).fetchall()
    table_sql = {str(row[0]): str(row[1] or "") for row in table_sql_rows}
    for table, column, expected in (
        (
            "notification_outbox",
            "status",
            NOTIFICATION_OUTBOX_STATUS_VALUES,
        ),
        (
            "notification_delivery_attempts",
            "status",
            DELIVERY_ATTEMPT_STATUS_VALUES,
        ),
    ):
        match = re.search(
            rf"\b{column}\b\s+[^,]*?\bCHECK\s*\(\s*{column}\s+IN\s*\((?P<values>[^)]*)\)\)",
            table_sql.get(table, ""),
            re.IGNORECASE | re.DOTALL,
        )
        actual = {
            value.replace("''", "'").upper()
            for value in re.findall(r"'((?:''|[^'])*)'", match.group("values"))
        } if match else set()
        if actual != expected:
            errors.append(
                f"{table}.status CHECK must allow exactly "
                + ", ".join(sorted(expected))
            )

    numeric_checks = (
        ("notification_outbox", "attempt_count", r"attempt_count\s*>=\s*0"),
        ("notification_outbox", "max_attempts", r"max_attempts\s*>\s*0"),
        ("notification_outbox", "lease_version", r"lease_version\s*>=\s*0"),
        ("notification_delivery_attempts", "attempt_no", r"attempt_no\s*>\s*0"),
        ("notification_delivery_attempts", "lease_version", r"lease_version\s*>=\s*0"),
    )
    for table, column, pattern in numeric_checks:
        if not re.search(rf"\bCHECK\s*\(\s*{pattern}\s*\)", table_sql.get(table, ""), re.IGNORECASE):
            errors.append(f"{table}.{column} lacks required numeric CHECK")

    index_rows = {
        str(row[1]): row
        for table in ("notification_outbox", "notification_delivery_attempts")
        for row in connection.execute(f"PRAGMA index_list('{table}')").fetchall()
    }
    missing_indexes: list[str] = []
    for index_name, (expected_columns, expected_unique) in NOTIFICATION_OUTBOX_INDEX_SPECS.items():
        row = index_rows.get(index_name)
        if row is None:
            missing_indexes.append(index_name)
            continue
        actual_unique = int(row[2]) == 1
        if actual_unique != expected_unique:
            errors.append(f"{index_name} unique={actual_unique}, expected {expected_unique}")
        actual_columns = tuple(
            str(index_row[2])
            for index_row in connection.execute(f"PRAGMA index_info('{index_name}')").fetchall()
        )
        if actual_columns != expected_columns:
            errors.append(f"{index_name} columns={actual_columns}, expected {expected_columns}")

    unique_attempt_key = False
    for row in connection.execute("PRAGMA index_list('notification_delivery_attempts')").fetchall():
        if int(row[2]) != 1:
            continue
        index_name = str(row[1])
        actual_columns = tuple(
            str(index_row[2])
            for index_row in connection.execute(f"PRAGMA index_info('{index_name}')").fetchall()
        )
        if actual_columns == ("notification_id", "attempt_no"):
            unique_attempt_key = True
            break
    if not unique_attempt_key:
        errors.append(
            "notification_delivery_attempts lacks UNIQUE(notification_id, attempt_no)"
        )
    return tuple(sorted(missing_indexes))


def _check_price_batch_constraints(
    connection: sqlite3.Connection,
    errors: list[str],
) -> tuple[str, ...]:
    """Validate the v7 batch ledger, bindings, and active identity locks."""

    batch_info = connection.execute("PRAGMA table_info(shadowbot_batches)").fetchall()
    batch_columns = {str(row[1]): row for row in batch_info}
    if not batch_columns.get("batch_id") or int(batch_columns["batch_id"][5]) != 1:
        errors.append("shadowbot_batches.batch_id is not the primary key")

    item_info = connection.execute("PRAGMA table_info(shadowbot_batch_items)").fetchall()
    item_pk = tuple(
        name
        for _, name in sorted(
            (int(row[5]), str(row[1])) for row in item_info if int(row[5]) > 0
        )
    )
    if item_pk != ("batch_id", "item_id"):
        errors.append("shadowbot_batch_items primary key must be (batch_id, item_id)")

    item_foreign_keys = {
        (str(row[3]), str(row[2]), str(row[4]))
        for row in connection.execute("PRAGMA foreign_key_list(shadowbot_batch_items)").fetchall()
    }
    for column, target_table, target_column in (
        ("batch_id", "shadowbot_batches", "batch_id"),
        ("task_id", "tasks", "task_id"),
        ("review_task_id", "review_tasks", "review_task_id"),
        ("operation_id", "shadowbot_operations", "operation_id"),
    ):
        if (column, target_table, target_column) not in item_foreign_keys:
            errors.append(f"missing foreign key {column} -> {target_table}({target_column})")

    sql_rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
        "AND name IN ('shadowbot_batches', 'shadowbot_batch_items', "
        "'shadowbot_batch_control_events')"
    ).fetchall()
    table_sql = {str(row[0]): str(row[1] or "") for row in sql_rows}
    for table, expected in (
        ("shadowbot_batches", {status.value for status in BatchStatus}),
        ("shadowbot_batch_items", {status.value for status in BatchItemStatus}),
    ):
        match = re.search(
            r"\bstatus\b\s+[^,]*?\bCHECK\s*\(\s*status\s+IN\s*\((?P<values>[^)]*)\)\)",
            table_sql.get(table, ""),
            re.IGNORECASE | re.DOTALL,
        )
        actual = {
            value.replace("''", "'").upper()
            for value in re.findall(r"'((?:''|[^'])*)'", match.group("values"))
        } if match else set()
        if actual != expected:
            errors.append(f"{table}.status CHECK must allow exactly " + ", ".join(sorted(expected)))

    for column in (
        "source_snapshot_max_age_seconds",
        "pending_count",
        "ready_count",
        "running_count",
        "processed_count",
        "previewed_count",
        "verified_count",
        "failed_count",
        "skipped_count",
        "cancelled_count",
        "needs_reconciliation_count",
        "reconciled_item_count",
    ):
        pattern = (
            rf"\bCHECK\s*\(\s*{column}\s*=\s*300\s*\)"
            if column == "source_snapshot_max_age_seconds"
            else rf"\bCHECK\s*\(\s*{column}\s*>=\s*0\s*\)"
        )
        if not re.search(pattern, table_sql.get("shadowbot_batches", ""), re.IGNORECASE):
            errors.append(f"shadowbot_batches.{column} lacks required CHECK")
    if not re.search(
        r"\bCHECK\s*\(\s*contract_version\s*=\s*3\s*\)",
        table_sql.get("shadowbot_batches", ""),
        re.IGNORECASE,
    ):
        errors.append("shadowbot_batches.contract_version lacks CHECK (contract_version = 3)")
    if not re.search(
        r"\bCHECK\s*\(\s*ordinal\s*>\s*0\s*\)",
        table_sql.get("shadowbot_batch_items", ""),
        re.IGNORECASE,
    ):
        errors.append("shadowbot_batch_items.ordinal lacks CHECK (ordinal > 0)")
    if not re.search(
        r"\bCHECK\s*\(\s*capture_evidence\s+IN\s*\(\s*0\s*,\s*1\s*\)\s*\)",
        table_sql.get("shadowbot_batches", ""),
        re.IGNORECASE,
    ):
        errors.append("shadowbot_batches.capture_evidence lacks boolean CHECK")
    control_sql = table_sql.get("shadowbot_batch_control_events", "")
    if not re.search(
        r"\bCHECK\s*\(\s*action\s+IN\s*\(\s*'PAUSE'\s*,\s*'RESUME'\s*,\s*'CANCEL_PENDING'\s*\)\s*\)",
        control_sql,
        re.IGNORECASE,
    ):
        errors.append("shadowbot_batch_control_events.action lacks required CHECK")
    if not re.search(
        r"\bCHECK\s*\(\s*applied\s+IN\s*\(\s*0\s*,\s*1\s*\)\s*\)",
        control_sql,
        re.IGNORECASE,
    ):
        errors.append("shadowbot_batch_control_events.applied lacks boolean CHECK")

    index_rows = {
        str(row[1]): row
        for table in (
            "shadowbot_batches",
            "shadowbot_batch_items",
            "shadowbot_batch_control_events",
            "shadowbot_operations",
        )
        for row in connection.execute(f"PRAGMA index_list('{table}')").fetchall()
    }
    missing_indexes: list[str] = []
    for index_name, (expected_columns, expected_unique) in PRICE_BATCH_INDEX_SPECS.items():
        row = index_rows.get(index_name)
        if row is None:
            missing_indexes.append(index_name)
            continue
        actual_unique = int(row[2]) == 1
        if actual_unique != expected_unique:
            errors.append(f"{index_name} unique={actual_unique}, expected {expected_unique}")
        actual_columns = tuple(
            str(index_row[2])
            for index_row in connection.execute(f"PRAGMA index_info('{index_name}')").fetchall()
        )
        if actual_columns != expected_columns:
            errors.append(f"{index_name} columns={actual_columns}, expected {expected_columns}")

    for index_name in (
        "ux_shadowbot_operations_active_write_identity",
        "ux_shadowbot_operations_active_page_identity",
    ):
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        index_sql = str(sql_row[0] or "") if sql_row else ""
        match = re.search(r"\bstatus\s+IN\s*\((?P<values>[^)]*)\)", index_sql, re.IGNORECASE)
        actual_states = {
            value.replace("''", "'").upper()
            for value in re.findall(r"'((?:''|[^'])*)'", match.group("values"))
        } if match else set()
        if actual_states != WRITE_LOCK_STATES:
            errors.append(f"{index_name} must use the complete WRITE_LOCK_STATES set")
    reconcile_index = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' "
        "AND name = 'ux_shadowbot_batch_items_reconcile_attempt_id'"
    ).fetchone()
    reconcile_sql = str(reconcile_index[0] or "") if reconcile_index else ""
    if not re.search(
        r"\breconcile_attempt_id\s*<>\s*''",
        reconcile_sql,
        re.IGNORECASE,
    ):
        errors.append(
            "ux_shadowbot_batch_items_reconcile_attempt_id must exclude empty identifiers"
        )
    return tuple(sorted(missing_indexes))

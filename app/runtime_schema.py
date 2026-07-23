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


LATEST_RUNTIME_SCHEMA_VERSION = 12
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
        "listing_status",
        "shadowbot_commit_batches",
        "shadowbot_commit_batch_items",
        "shadowbot_write_locks",
        "shadowbot_commit_result_receipts",
    }
)

V7_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "listing_status": (
        "listing_status_id",
        "platform_name",
        "internal_sku",
        "variety",
        "current_price",
        "platform_stock_qty",
        "sold_qty",
        "online_status",
        "source",
        "updated_at",
    ),
}

V8_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "listing_status": (
        "inventory_source",
        "inventory_observed_at",
        "inventory_source_attempt_id",
    ),
}

V9_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "listing_status": ("grade",),
}

V10_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "tasks": ("expected_old_price",),
}

V11_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
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
        "preflight_row",
        "preflight_price",
        "execution_ordinal",
        "submit_attempted",
        "actual_price",
        "status",
        "error_code",
        "error_message",
        "updated_at",
    ),
}

V12_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "shadowbot_commit_batch_items": (
        "item_id",
        "operation_id",
        "item_execution_attempt_id",
        "write_identity_key",
        "page_identity_key",
        "side_effect_state",
        "preflight_observed_at",
        "submit_intent_at",
        "submit_clicked_at",
        "readback_observed_at",
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
        "source_result_path",
        "accepted_at",
        "ack_state",
        "ack_updated_at",
        "last_projection_error",
    ),
}

V12_INDEX_SPECS: Mapping[str, tuple[str, ...]] = {
    "ux_shadowbot_commit_batch_items_item_id": ("item_id",),
    "ix_shadowbot_commit_batch_items_operation_id": ("operation_id",),
    "ux_shadowbot_commit_batch_items_attempt_id": ("item_execution_attempt_id",),
    "ux_shadowbot_write_locks_operation_id": ("operation_id",),
    "ix_shadowbot_commit_result_receipts_batch_id": ("batch_id",),
    "ix_shadowbot_commit_result_receipts_ack_state": ("ack_state", "accepted_at"),
}

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

    The check is deliberately exact: a database with a migration row for v6
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
        for table, required in {
            **V5_REQUIRED_COLUMNS,
            **V6_REQUIRED_COLUMNS,
            **V7_REQUIRED_COLUMNS,
            **V8_REQUIRED_COLUMNS,
            **V9_REQUIRED_COLUMNS,
            **V10_REQUIRED_COLUMNS,
            **V11_REQUIRED_COLUMNS,
            **V12_REQUIRED_COLUMNS,
        }.items():
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
        if "listing_status" in tables:
            listing_sql_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'listing_status'"
            ).fetchone()
            listing_sql = str(listing_sql_row[0] or "") if listing_sql_row else ""
            if not re.search(
                r"CHECK\s*\(\s*platform_stock_qty\s*>=\s*0\s*\)",
                listing_sql,
                re.IGNORECASE,
            ):
                constraint_errors.append("listing_status.platform_stock_qty lacks CHECK >= 0")
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
        for index_name, expected_columns in V12_INDEX_SPECS.items():
            index_row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
                (index_name,),
            ).fetchone()
            if index_row is None:
                missing_index_names.add(index_name)
                continue
            actual_columns = tuple(
                str(row[2])
                for row in connection.execute(f"PRAGMA index_info({index_name})").fetchall()
            )
            if actual_columns != expected_columns:
                constraint_errors.append(
                    f"{index_name} columns expected {expected_columns}, actual {actual_columns}"
                )
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
            missing_indexes=tuple(sorted(RETRY_AUTHORIZATION_INDEXES | NOTIFICATION_OUTBOX_INDEXES)),
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
    """Raise a diagnostic error unless the connection has the exact latest shape."""

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
    """Validate required keys, foreign keys, status/numeric checks, and indexes."""

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

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


LATEST_RUNTIME_SCHEMA_VERSION = 5
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

    The check is deliberately exact: a database with a migration row for v5
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
        for table, required in V5_REQUIRED_COLUMNS.items():
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
        if "retry_authorizations" in tables and "retry_authorizations" not in missing_tables:
            missing_indexes = _check_retry_authorization_constraints(connection, constraint_errors)
        else:
            missing_indexes = tuple(sorted(RETRY_AUTHORIZATION_INDEXES))

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
            missing_indexes=tuple(sorted(RETRY_AUTHORIZATION_INDEXES)),
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
    """Raise a diagnostic error unless the connection has the exact v5 shape."""

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
    foreign_key_pairs = {(str(row[3]), str(row[2])) for row in foreign_keys}
    for column, target in (
        ("operation_id", "shadowbot_operations"),
        ("source_execution_attempt_id", "shadowbot_execution_attempts"),
    ):
        if (column, target) not in foreign_key_pairs:
            errors.append(f"missing foreign key {column} -> {target}")

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

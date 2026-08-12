from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.repositories import sqlite_runtime_repository as runtime_module
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION


V17_TABLES = {
    "inventory_authority_state",
    "inventory_balances",
    "inventory_transactions",
    "inventory_sales_baselines",
    "inventory_alert_policies",
}


def _repository(tmp_path: Path) -> SQLiteRuntimeRepository:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    return repository


def _downgrade_to_v16(repository: SQLiteRuntimeRepository) -> None:
    with closing(repository.connect_write()) as connection, connection:
        for table_name in (
            "inventory_alert_policies",
            "inventory_sales_baselines",
            "inventory_transactions",
            "inventory_balances",
            "inventory_authority_state",
        ):
            connection.execute(f"DROP TABLE {table_name}")
        connection.execute(
            "DELETE FROM runtime_schema_migrations WHERE schema_version = 17"
        )


def test_v17_new_database_has_explicit_pre_cutover_authority(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)

    assert LATEST_RUNTIME_SCHEMA_VERSION == 17
    assert repository.schema_versions() == list(range(1, 18))
    health = repository.check_schema_health()
    assert health.ok, health.summary
    with closing(repository.connect_read()) as connection:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        authority = connection.execute(
            "SELECT authority_key, authority_mode, "
            "bootstrap_runtime_snapshot_sha256, "
            "bootstrap_sales_watermark_date, version "
            "FROM inventory_authority_state"
        ).fetchone()
    assert V17_TABLES <= tables
    assert tuple(authority) == (
        "REAL_INVENTORY",
        "PRE_CUTOVER",
        None,
        None,
        0,
    )


def test_v16_to_v17_migration_is_repeatable_and_preserves_v16_policy(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _downgrade_to_v16(repository)
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO emergency_offline_policies(
                policy_version, platform_name, emergency_ratio, created_at
            ) VALUES ('POLICY-V16', 'platform', '0.80', ?)
            """,
            ("2026-08-12T01:00:00+00:00",),
        )

    repository.init_schema()
    repository.init_schema()

    assert repository.check_schema_health().ok
    with closing(repository.connect_read()) as connection:
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM emergency_offline_policies "
                "WHERE policy_version = 'POLICY-V16'"
            ).fetchone()[0]
        )
        authority_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM inventory_authority_state"
            ).fetchone()[0]
        )
    assert count == 1
    assert authority_count == 1


def test_v17_migration_failure_rolls_back_all_inventory_tables_and_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    _downgrade_to_v16(repository)
    monkeypatch.setattr(
        runtime_module,
        "SCHEMA_V17_SQL",
        [*runtime_module.SCHEMA_V17_SQL, "THIS IS NOT VALID SQL"],
    )

    with pytest.raises(sqlite3.Error):
        repository.init_schema()

    with closing(repository.connect_read()) as connection:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        versions = [
            int(row["schema_version"])
            for row in connection.execute(
                "SELECT schema_version FROM runtime_schema_migrations "
                "ORDER BY schema_version"
            )
        ]
    assert not (V17_TABLES & tables)
    assert versions == list(range(1, 17))


def test_v17_rejects_invalid_authority_and_mutating_ledger(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE inventory_authority_state "
                "SET authority_mode = 'DB_AUTHORITY'"
            )
        connection.execute(
            """
            INSERT INTO inventory_transactions(
                transaction_id, internal_sku,
                inventory_before, inventory_delta, inventory_after,
                transaction_type, source_type, source_ref_id,
                reason, actor, supporting_refs_json,
                idempotency_key, request_sha256,
                balance_version_after, occurred_at, recorded_at
            ) VALUES (
                'TX-1', 'SKU-1', 0, 5, 5,
                'BOOTSTRAP', 'TEST', 'TEST-1',
                'fixture', 'test', '[]',
                'test:1', ?, 1, ?, ?
            )
            """,
            (
                "sha256:" + "a" * 64,
                "2026-08-12T01:00:00+00:00",
                "2026-08-12T01:00:00+00:00",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE inventory_transactions SET reason = 'changed'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM inventory_transactions")


def test_v17_health_detects_missing_append_only_trigger(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    trigger_name = "trg_inventory_transactions_append_only_update"
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(f"DROP TRIGGER {trigger_name}")

    health = repository.check_schema_health()

    assert not health.ok
    assert any(trigger_name in error for error in health.constraint_errors)

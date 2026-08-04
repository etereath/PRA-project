from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.repositories import sqlite_runtime_repository as runtime_module
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION


def _repository(tmp_path: Path) -> SQLiteRuntimeRepository:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    return repository


def _downgrade_to_v15(repository: SQLiteRuntimeRepository) -> None:
    with closing(repository.connect_write()) as connection, connection:
        connection.execute("DROP TABLE emergency_offline_policies")
        connection.execute(
            "DELETE FROM runtime_schema_migrations WHERE schema_version = 16"
        )


def test_v16_new_database_has_only_the_minimal_policy_table(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)

    assert LATEST_RUNTIME_SCHEMA_VERSION == 16
    assert repository.schema_versions() == list(range(1, 17))
    health = repository.check_schema_health()
    assert health.ok, health.summary

    with closing(repository.connect_read()) as connection:
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(emergency_offline_policies)"
            )
        }
    assert columns == {
        "policy_version",
        "platform_name",
        "emergency_ratio",
        "approved_by",
        "approved_at",
        "created_at",
        "retired_at",
    }


def test_v15_to_v16_migration_is_repeatable_and_preserves_v15_facts(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _downgrade_to_v15(repository)
    now = "2026-08-03T01:00:00+00:00"
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO operational_incidents(
                incident_id, dedupe_key, category, source_type, source_ref_id,
                severity, incident_status, blocks_finalization, platform_name,
                platform_trade_date, seller_operation_date, subject_type,
                subject_key, title, description, first_detected_at,
                last_detected_at, resolved_at, occurrence_count, created_at,
                updated_at
            ) VALUES (
                'INCIDENT-PRESERVED', 'incident-preserved', 'PRICE_ANOMALY',
                'PRODUCT_OBSERVATION', 'OBS-1', 'S4', 'OPEN', 0, 'platform',
                '2026-08-03', '2026-08-03', 'internal_sku', 'SKU-1',
                'price anomaly', '', ?, ?, NULL, 1, ?, ?
            )
            """,
            (now, now, now, now),
        )

    repository.init_schema()
    repository.init_schema()

    assert repository.schema_versions() == list(range(1, 17))
    assert repository.check_schema_health().ok
    with closing(repository.connect_read()) as connection:
        incident_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM operational_incidents "
                "WHERE incident_id = 'INCIDENT-PRESERVED'"
            ).fetchone()[0]
        )
        policy_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM emergency_offline_policies"
            ).fetchone()[0]
        )
    assert incident_count == 1
    assert policy_count == 0


def test_v16_migration_failure_rolls_back_table_and_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    _downgrade_to_v15(repository)
    monkeypatch.setattr(
        runtime_module,
        "SCHEMA_V16_SQL",
        [*runtime_module.SCHEMA_V16_SQL, "THIS IS NOT VALID SQL"],
    )

    with pytest.raises(sqlite3.Error):
        repository.init_schema()

    with closing(repository.connect_read()) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'emergency_offline_policies'"
        ).fetchone()
        versions = [
            int(row["schema_version"])
            for row in connection.execute(
                "SELECT schema_version FROM runtime_schema_migrations "
                "ORDER BY schema_version"
            )
        ]
    assert table is None
    assert versions == list(range(1, 16))


def test_v16_database_rejects_ratio_mutation_and_policy_delete(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO emergency_offline_policies(
                    policy_version, platform_name, emergency_ratio, created_at
                ) VALUES ('BAD-RATIO', 'platform', '0.79', ?)
                """,
                ("2026-08-03T01:00:00+00:00",),
            )
        connection.execute(
            """
            INSERT INTO emergency_offline_policies(
                policy_version, platform_name, emergency_ratio, created_at
            ) VALUES ('POLICY-1', 'platform', '0.80', ?)
            """,
            ("2026-08-03T01:00:00+00:00",),
        )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            connection.execute(
                "DELETE FROM emergency_offline_policies WHERE policy_version = 'POLICY-1'"
            )


def test_v16_health_detects_missing_lifecycle_trigger(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    trigger_name = "trg_emergency_offline_policies_lifecycle_update"
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(f"DROP TRIGGER {trigger_name}")

    health = repository.check_schema_health()

    assert not health.ok
    assert any(trigger_name in error for error in health.constraint_errors)

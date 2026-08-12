from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.enums import IncidentCategory, IncidentEventType
from app.repositories import sqlite_runtime_repository as runtime_module
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION

V15_CATEGORY_VALUES = {item.value for item in IncidentCategory}
V15_EVENT_TYPE_VALUES = {item.value for item in IncidentEventType}


def _repository(tmp_path: Path) -> SQLiteRuntimeRepository:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    return repository


def _insert_incident(
    connection,
    *,
    incident_id: str = "INCIDENT-V15",
    category: str = "SCAN_INCOMPLETE",
) -> None:
    now = "2026-08-02T01:00:00+00:00"
    connection.execute(
        """
        INSERT INTO operational_incidents(
            incident_id, dedupe_key, category,
            source_type, source_ref_id, severity, incident_status,
            blocks_finalization, platform_name, platform_trade_date,
            seller_operation_date, subject_type, subject_key,
            title, description, first_detected_at, last_detected_at,
            resolved_at, created_at, updated_at
        ) VALUES (
            ?, ?, ?,
            'SCAN', 'RUN-1', 'S2', 'OPEN',
            1, 'platform', '2026-08-02',
            '2026-08-02', 'PLATFORM', 'platform',
            'incident', 'description', ?, ?,
            NULL, ?, ?
        )
        """,
        (
            incident_id,
            incident_id.lower(),
            category,
            now,
            now,
            now,
            now,
        ),
    )


def _insert_event(
    connection,
    *,
    event_id: str = "EVENT-V15",
    event_key: str = "event-v15",
    event_type: str = "DETECTED",
    incident_id: str = "INCIDENT-V15",
) -> None:
    now = "2026-08-02T01:00:00+00:00"
    connection.execute(
        """
        INSERT INTO operational_incident_events(
            event_id, event_key, incident_id, event_type,
            occurred_at, source_type, source_ref_id,
            from_status, to_status, severity,
            event_payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, 'SCAN', 'RUN-1',
                  NULL, 'OPEN', 'S2', '{}', ?)
        """,
        (event_id, event_key, incident_id, event_type, now, now),
    )


def test_v15_new_database_has_minimal_incident_extension(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)

    assert LATEST_RUNTIME_SCHEMA_VERSION >= 15
    assert repository.schema_versions() == list(
        range(1, LATEST_RUNTIME_SCHEMA_VERSION + 1)
    )
    health = repository.check_schema_health()
    assert health.ok, health.summary

    with closing(repository.connect_read()) as connection:
        incident_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(operational_incidents)"
            )
        }
        event_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(operational_incident_events)"
            )
        }
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "occurrence_count" in incident_columns
    assert "operational_incident_events" in tables
    assert event_columns == {
        "event_id",
        "event_key",
        "incident_id",
        "event_type",
        "occurred_at",
        "source_type",
        "source_ref_id",
        "from_status",
        "to_status",
        "severity",
        "event_payload_json",
        "created_at",
    }


def test_v15_accepts_exact_category_and_event_type_sets(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        for index, category in enumerate(sorted(V15_CATEGORY_VALUES)):
            _insert_incident(
                connection,
                incident_id=f"INCIDENT-CATEGORY-{index}",
                category=category,
            )
        _insert_incident(connection)
        for index, event_type in enumerate(sorted(V15_EVENT_TYPE_VALUES)):
            _insert_event(
                connection,
                event_id=f"EVENT-TYPE-{index}",
                event_key=f"event-type-{index}",
                event_type=event_type,
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_incident(
                connection,
                incident_id="INCIDENT-BAD-CATEGORY",
                category="UNKNOWN",
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_event(
                connection,
                event_id="EVENT-BAD-TYPE",
                event_key="event-bad-type",
                event_type="UNKNOWN",
            )


def test_v15_incident_events_are_append_only_unique_and_parent_bound(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        _insert_incident(connection)
        _insert_event(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_event(
                connection,
                event_id="EVENT-REPLAY",
                event_key="event-v15",
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE operational_incident_events SET severity = 'S3'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM operational_incident_events")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_event(
                connection,
                event_id="EVENT-NO-PARENT",
                event_key="event-no-parent",
                incident_id="INCIDENT-MISSING",
            )


def test_v14_to_v15_migration_preserves_incident_and_notification(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _downgrade_fixture_to_v14(repository)
    now = "2026-08-02T01:00:00+00:00"
    with closing(repository.connect_write()) as connection, connection:
        _insert_incident(connection)
        connection.execute(
            """
            INSERT INTO incident_notification_state(
                incident_id, channel, notification_count,
                last_notified_at, next_notification_at,
                escalation_state, payload_sha256, updated_at
            ) VALUES (
                'INCIDENT-V15', 'feishu', 1,
                ?, NULL, 'INITIAL', 'sha256:test', ?
            )
            """,
            (now, now),
        )

    repository.init_schema()
    repository.init_schema()

    assert repository.schema_versions() == list(
        range(1, LATEST_RUNTIME_SCHEMA_VERSION + 1)
    )
    assert repository.check_schema_health().ok
    with closing(repository.connect_read()) as connection:
        incident = connection.execute(
            """
            SELECT category, occurrence_count
            FROM operational_incidents
            WHERE incident_id = 'INCIDENT-V15'
            """
        ).fetchone()
        notification_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM incident_notification_state "
                "WHERE incident_id = 'INCIDENT-V15'"
            ).fetchone()[0]
        )
        foreign_key_rows = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
    assert tuple(incident) == ("SCAN_INCOMPLETE", 1)
    assert notification_count == 1
    assert foreign_key_rows == []


def test_v15_migration_failure_rolls_back_parent_and_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    _downgrade_fixture_to_v14(repository)
    with closing(repository.connect_write()) as connection, connection:
        _insert_incident(connection)
    monkeypatch.setattr(
        runtime_module,
        "SCHEMA_V15_SQL",
        [*runtime_module.SCHEMA_V15_SQL, "THIS IS NOT VALID SQL"],
    )

    with pytest.raises(sqlite3.Error):
        repository.init_schema()

    with closing(repository.connect_read()) as connection:
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(operational_incidents)"
            )
        }
        incident_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM operational_incidents"
            ).fetchone()[0]
        )
        versions = [
            int(row["schema_version"])
            for row in connection.execute(
                "SELECT schema_version FROM runtime_schema_migrations "
                "ORDER BY schema_version"
            )
        ]
    assert "occurrence_count" not in columns
    assert incident_count == 1
    assert versions == list(range(1, 15))


def test_v15_health_detects_missing_append_only_trigger(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    trigger_name = "trg_operational_incident_events_append_only_update"
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(f"DROP TRIGGER {trigger_name}")

    health = repository.check_schema_health()

    assert not health.ok
    assert any(trigger_name in error for error in health.constraint_errors)


def _downgrade_fixture_to_v14(
    repository: SQLiteRuntimeRepository,
) -> None:
    with closing(repository.connect_write()) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table_name in (
                "inventory_alert_policies",
                "inventory_sales_baselines",
                "inventory_transactions",
                "inventory_balances",
                "inventory_authority_state",
            ):
                connection.execute(f"DROP TABLE {table_name}")
            connection.execute("DROP TABLE emergency_offline_policies")
            connection.execute("DROP TABLE operational_incident_events")
            connection.execute(
                """
                CREATE TABLE operational_incidents_v14_old (
                    incident_id TEXT PRIMARY KEY,
                    dedupe_key TEXT NOT NULL,
                    category TEXT NOT NULL CHECK (category IN (
                        'PLATFORM_LOGIN', 'PLATFORM_NETWORK', 'PAGE_STRUCTURE',
                        'SCAN_INCOMPLETE', 'WORKER_UNAVAILABLE', 'QUEUE_BACKLOG',
                        'PRODUCT_MAPPING', 'PRICE_ANOMALY', 'INVENTORY_ANOMALY',
                        'ORDER_PAGE_UNAVAILABLE', 'ORDER_DATA_INCONSISTENT',
                        'SALES_ESTIMATE_LOW_CONFIDENCE', 'NOTIFICATION_FAILURE',
                        'WRITE_UNKNOWN'
                    )),
                    source_type TEXT NOT NULL,
                    source_ref_id TEXT NOT NULL DEFAULT '',
                    severity TEXT NOT NULL CHECK (
                        severity IN ('S0', 'S1', 'S2', 'S3', 'S4')
                    ),
                    incident_status TEXT NOT NULL CHECK (incident_status IN (
                        'OPEN', 'RETRYING', 'WAITING_HUMAN', 'ACKNOWLEDGED',
                        'AUTO_PROTECTING', 'RESOLVED', 'CLOSED'
                    )),
                    blocks_finalization INTEGER NOT NULL DEFAULT 0 CHECK (
                        blocks_finalization IN (0, 1)
                    ),
                    platform_name TEXT,
                    platform_trade_date TEXT,
                    seller_operation_date TEXT,
                    subject_type TEXT NOT NULL,
                    subject_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    first_detected_at TEXT NOT NULL,
                    last_detected_at TEXT NOT NULL,
                    resolved_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (
                        (
                            incident_status IN ('RESOLVED', 'CLOSED')
                            AND resolved_at IS NOT NULL
                        )
                        OR (
                            incident_status NOT IN ('RESOLVED', 'CLOSED')
                            AND resolved_at IS NULL
                        )
                    )
                )
                """
            )
            connection.execute(
                """
                INSERT INTO operational_incidents_v14_old(
                    incident_id, dedupe_key, category,
                    source_type, source_ref_id, severity, incident_status,
                    blocks_finalization, platform_name, platform_trade_date,
                    seller_operation_date, subject_type, subject_key,
                    title, description, first_detected_at, last_detected_at,
                    resolved_at, created_at, updated_at
                )
                SELECT incident_id, dedupe_key, category,
                       source_type, source_ref_id, severity, incident_status,
                       blocks_finalization, platform_name, platform_trade_date,
                       seller_operation_date, subject_type, subject_key,
                       title, description, first_detected_at, last_detected_at,
                       resolved_at, created_at, updated_at
                FROM operational_incidents
                """
            )
            connection.execute("DROP TABLE operational_incidents")
            connection.execute(
                "ALTER TABLE operational_incidents_v14_old "
                "RENAME TO operational_incidents"
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX ux_operational_incidents_open_dedupe
                ON operational_incidents(dedupe_key)
                WHERE resolved_at IS NULL
                """
            )
            connection.execute(
                """
                CREATE INDEX ix_operational_incidents_status
                ON operational_incidents(
                    incident_status, severity, last_detected_at
                )
                """
            )
            connection.execute(
                "DELETE FROM runtime_schema_migrations WHERE schema_version >= 15"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

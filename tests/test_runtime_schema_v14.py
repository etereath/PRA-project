from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.enums import TaskActionType, TaskOriginType, TaskStatus
from app.models import Task
from app.repositories import sqlite_runtime_repository as runtime_module
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.runtime_schema import LATEST_RUNTIME_SCHEMA_VERSION


V14_TABLES = {
    "operational_time_policies",
    "automation_jobs",
    "automation_runs",
    "automation_run_events",
    "automation_run_links",
    "product_observation_batches",
    "product_observation_items",
    "order_observation_batches",
    "order_observation_items",
    "sales_estimate_segments",
    "platform_trade_day_summaries",
    "platform_trade_day_summary_events",
    "platform_trade_day_summary_inputs",
    "operational_incidents",
    "incident_notification_state",
}

V14_DROP_ORDER = (
    "incident_notification_state",
    "operational_incidents",
    "platform_trade_day_summary_inputs",
    "platform_trade_day_summary_events",
    "platform_trade_day_summaries",
    "sales_estimate_segments",
    "order_observation_items",
    "order_observation_batches",
    "product_observation_items",
    "product_observation_batches",
    "automation_run_links",
    "automation_run_events",
    "automation_runs",
    "automation_jobs",
    "operational_time_policies",
)

V14_TASK_COLUMNS = (
    "origin_type",
    "origin_ref_id",
    "approval_policy",
    "policy_version",
    "platform_trade_date",
    "seller_operation_date",
    "seller_phase",
    "time_policy_version",
)


def _repository(tmp_path: Path) -> SQLiteRuntimeRepository:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.sqlite3")
    repository.init_schema()
    return repository


def test_v14_new_database_has_frozen_tables_policy_and_task_origin(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)

    assert LATEST_RUNTIME_SCHEMA_VERSION == 14
    assert repository.schema_versions() == list(range(1, 15))
    health = repository.check_schema_health()
    assert health.ok, health.summary

    with closing(repository.connect_read()) as connection:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert V14_TABLES <= tables
        policy = connection.execute(
            """
            SELECT timezone_name, platform_cutoff_local_time,
                   seller_cutoff_local_time, peak_start_local_time,
                   effective_to
            FROM operational_time_policies
            WHERE policy_version = 'CN_SINGLE_PLATFORM_2026_V1'
            """
        ).fetchone()
        assert tuple(policy) == (
            "Asia/Shanghai",
            "18:00:00",
            "20:00:00",
            "16:00:00",
            None,
        )

    task = Task(
        task_id="TASK-V14-MANUAL",
        internal_sku="SKU-1",
        platform_name="platform",
        action_type=TaskActionType.MANUAL_REVIEW,
        priority=10,
        task_status=TaskStatus.PENDING,
        created_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )
    assert repository.insert_task(task) == 1
    loaded = repository.get_task(task.task_id)
    assert loaded is not None
    assert loaded.origin_type is TaskOriginType.MANUAL


def test_v14_database_rejects_invalid_task_origin(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    now = "2026-07-29T12:00:00+00:00"

    with closing(repository.connect_write()) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO tasks(
                    task_id, scope_type, scope_key, action_type,
                    priority, task_status, created_at, updated_at,
                    origin_type
                ) VALUES (
                    'TASK-BAD-ORIGIN', 'sku', 'SKU-1', 'manual_review',
                    10, 'pending', ?, ?, 'AGENT'
                )
                """,
                (now, now),
            )


@pytest.mark.parametrize(
    "origin_type",
    [TaskOriginType.LEGACY, TaskOriginType.SYSTEM_EMERGENCY],
)
def test_v14_task_repository_blocks_reserved_origins(
    tmp_path: Path,
    origin_type: TaskOriginType,
) -> None:
    repository = _repository(tmp_path)
    task = Task(
        task_id=f"TASK-{origin_type.value}",
        internal_sku="SKU-1",
        platform_name="platform",
        action_type=TaskActionType.MANUAL_REVIEW,
        priority=10,
        task_status=TaskStatus.PENDING,
        created_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        origin_type=origin_type,
    )

    with pytest.raises(ValueError, match=origin_type.value):
        repository.insert_task(task)


def test_v14_automation_task_requires_origin_reference(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    task = Task(
        task_id="TASK-AUTOMATION",
        internal_sku="SKU-1",
        platform_name="platform",
        action_type=TaskActionType.MANUAL_REVIEW,
        priority=10,
        task_status=TaskStatus.PENDING,
        created_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        origin_type=TaskOriginType.AUTOMATION,
    )

    with pytest.raises(ValueError, match="origin_ref_id"):
        repository.insert_task(task)


@pytest.mark.parametrize(
    (
        "fact_source",
        "quality_level",
        "summary_status",
        "sold_qty",
        "order_count",
        "amount",
        "finalized_at",
    ),
    [
        (
            "LEGACY",
            "UNAVAILABLE",
            "PROVISIONAL",
            None,
            None,
            None,
            None,
        ),
        (
            None,
            "UNAVAILABLE",
            "PROVISIONAL",
            0,
            None,
            None,
            None,
        ),
        (
            "SCAN_ESTIMATED",
            "ORDER_COMPLETE",
            "PROVISIONAL",
            1,
            None,
            None,
            None,
        ),
        (
            "ORDER_OBSERVED",
            "ORDER_PARTIAL",
            "FINAL",
            1,
            1,
            "10.00",
            "2026-07-29T12:00:00+00:00",
        ),
    ],
)
def test_v14_database_rejects_illegal_fact_quality_or_final_combinations(
    tmp_path: Path,
    fact_source: str | None,
    quality_level: str,
    summary_status: str,
    sold_qty: int | None,
    order_count: int | None,
    amount: str | None,
    finalized_at: str | None,
) -> None:
    repository = _repository(tmp_path)

    with closing(repository.connect_write()) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_summary(
                connection,
                summary_id="SUMMARY-INVALID",
                series_id="SERIES-INVALID",
                fact_source=fact_source,
                quality_level=quality_level,
                summary_status=summary_status,
                sold_qty=sold_qty,
                order_count=order_count,
                amount=amount,
                finalized_at=finalized_at,
            )


def test_v14_database_enforces_one_current_summary_per_series(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)

    with closing(repository.connect_write()) as connection, connection:
        _insert_summary(
            connection,
            summary_id="SUMMARY-1",
            series_id="SERIES-1",
            fact_source=None,
            quality_level="UNAVAILABLE",
            summary_status="PROVISIONAL",
            sold_qty=None,
            order_count=None,
            amount=None,
            finalized_at=None,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_summary(
                connection,
                summary_id="SUMMARY-2",
                series_id="SERIES-1",
                fact_source=None,
                quality_level="UNAVAILABLE",
                summary_status="PROVISIONAL",
                sold_qty=None,
                order_count=None,
                amount=None,
                finalized_at=None,
                version_no=2,
            )


def test_v14_database_trigger_rejects_initial_final_and_status_skip(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)

    with closing(repository.connect_write()) as connection, connection:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="must start PROVISIONAL",
        ):
            _insert_summary(
                connection,
                summary_id="SUMMARY-DIRECT-FINAL",
                series_id="SERIES-DIRECT-FINAL",
                fact_source="ORDER_OBSERVED",
                quality_level="ORDER_COMPLETE",
                summary_status="FINAL",
                sold_qty=1,
                order_count=1,
                amount="10.00",
                finalized_at="2026-07-29T12:00:00+00:00",
            )
        _insert_summary(
            connection,
            summary_id="SUMMARY-SKIP",
            series_id="SERIES-SKIP",
            fact_source="SCAN_ESTIMATED",
            quality_level="SCAN_ESTIMATED_HIGH",
            summary_status="PROVISIONAL",
            sold_qty=1,
            order_count=None,
            amount=None,
            finalized_at=None,
        )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="illegal trade-day summary transition",
        ):
            connection.execute(
                """
                UPDATE platform_trade_day_summaries
                SET fact_source = 'ORDER_OBSERVED',
                    quality_level = 'ORDER_COMPLETE',
                    summary_status = 'RECONCILED',
                    order_count = 1,
                    seller_received_amount = '10.00'
                WHERE summary_id = 'SUMMARY-SKIP'
                """
            )


def test_v14_health_detects_missing_required_index(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            "DROP INDEX ux_trade_day_summaries_current"
        )

    health = repository.check_schema_health()

    assert not health.ok
    assert "ux_trade_day_summaries_current" in health.missing_indexes


def test_v13_to_v14_migration_backfills_legacy_without_guessing_dates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime-v13.sqlite3"
    repository = SQLiteRuntimeRepository(path)
    repository.init_schema()
    now = "2026-07-29T12:00:00+00:00"
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO tasks(
                task_id, trade_date, scope_type, scope_key,
                action_type, priority, task_status,
                created_at, updated_at, origin_type,
                platform_trade_date, seller_operation_date
            ) VALUES (
                'TASK-HISTORICAL', '2026-07-28', 'sku', 'SKU-1',
                'manual_review', 10, 'pending',
                ?, ?, 'MANUAL', '2099-01-01', '2099-01-02'
            )
            """,
            (now, now),
        )
    _downgrade_fixture_to_v13(repository)

    repository.init_schema()

    assert repository.schema_versions() == list(range(1, 15))
    assert repository.check_schema_health().ok
    with closing(repository.connect_read()) as connection:
        row = connection.execute(
            """
            SELECT trade_date, origin_type,
                   platform_trade_date, seller_operation_date,
                   seller_phase, time_policy_version
            FROM tasks
            WHERE task_id = 'TASK-HISTORICAL'
            """
        ).fetchone()
        assert tuple(row) == (
            "2026-07-28",
            "LEGACY",
            None,
            None,
            None,
            None,
        )
        assert connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall() == []


def test_v13_to_v14_failure_rolls_back_all_schema_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "runtime-v13-failure.sqlite3"
    repository = SQLiteRuntimeRepository(path)
    repository.init_schema()
    _downgrade_fixture_to_v13(repository)
    monkeypatch.setattr(
        runtime_module,
        "SCHEMA_V14_SQL",
        [*runtime_module.SCHEMA_V14_SQL, "THIS IS NOT VALID SQL"],
    )

    with pytest.raises(sqlite3.Error):
        repository.init_schema()

    with closing(repository.connect_read()) as connection:
        versions = [
            int(row["schema_version"])
            for row in connection.execute(
                """
                SELECT schema_version
                FROM runtime_schema_migrations
                ORDER BY schema_version
                """
            )
        ]
        assert versions == list(range(1, 14))
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert not (V14_TABLES & tables)
        task_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(tasks)")
        }
        assert not (set(V14_TASK_COLUMNS) & task_columns)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def _downgrade_fixture_to_v13(
    repository: SQLiteRuntimeRepository,
) -> None:
    with closing(repository.connect_write()) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table in V14_DROP_ORDER:
                connection.execute(f"DROP TABLE {table}")
            for column in V14_TASK_COLUMNS:
                connection.execute(
                    f"ALTER TABLE tasks DROP COLUMN {column}"
                )
            connection.execute(
                "DELETE FROM runtime_schema_migrations WHERE schema_version = 14"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")


def _insert_summary(
    connection,
    *,
    summary_id: str,
    series_id: str,
    fact_source: str | None,
    quality_level: str,
    summary_status: str,
    sold_qty: int | None,
    order_count: int | None,
    amount: str | None,
    finalized_at: str | None,
    version_no: int = 1,
) -> None:
    now = "2026-07-29T12:00:00+00:00"
    connection.execute(
        """
        INSERT INTO platform_trade_day_summaries(
            summary_id, summary_series_id, version_no,
            supersedes_summary_id, is_current,
            platform_name, platform_trade_date,
            seller_operation_date, seller_phase,
            scope_type, scope_key,
            fact_source, quality_level, summary_status,
            sold_qty, order_count, seller_received_amount,
            quality_reason, source_proportions_json,
            input_manifest_sha256, mapping_version,
            algorithm_version, time_policy_version,
            finalized_at, created_at, updated_at
        ) VALUES (
            ?, ?, ?, NULL, 1,
            'platform', '2026-07-29',
            '2026-07-29', 'NORMAL_SALES',
            'PLATFORM', 'platform',
            ?, ?, ?,
            ?, ?, ?,
            '', '{}',
            'sha256:input', 'mapping-v1',
            'algorithm-v1', 'CN_SINGLE_PLATFORM_2026_V1',
            ?, ?, ?
        )
        """,
        (
            summary_id,
            series_id,
            version_no,
            fact_source,
            quality_level,
            summary_status,
            sold_qty,
            order_count,
            amount,
            finalized_at,
            now,
            now,
        ),
    )

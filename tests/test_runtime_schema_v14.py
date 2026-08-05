from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest

from app.enums import (
    AutomationRunStatus,
    IncidentCategory,
    IncidentStatus,
    TaskActionType,
    TaskOriginType,
    TaskStatus,
)
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
    "emergency_offline_policies",
    "operational_incident_events",
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

    assert LATEST_RUNTIME_SCHEMA_VERSION == 16
    assert repository.schema_versions() == list(range(1, 17))
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
                   effective_from, effective_to
            FROM operational_time_policies
            WHERE policy_version = 'CN_SINGLE_PLATFORM_2026_V1'
            """
        ).fetchone()
        assert tuple(policy) == (
            "Asia/Shanghai",
            "18:00:00",
            "20:00:00",
            "16:00:00",
            "2025-12-31T16:00:00+00:00",
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
        origin_type=TaskOriginType.MANUAL,
        origin_ref_id="test-harness:runtime-schema-v14:TASK-V14-MANUAL",
    )
    assert repository.insert_task(task) == 1
    loaded = repository.get_task(task.task_id)
    assert loaded is not None
    assert loaded.origin_type is TaskOriginType.MANUAL


def test_task_model_requires_explicit_origin() -> None:
    with pytest.raises(TypeError, match="origin_type"):
        Task(
            task_id="TASK-MISSING-ORIGIN",
            internal_sku="SKU-1",
            platform_name="platform",
            action_type=TaskActionType.MANUAL_REVIEW,
            priority=10,
            task_status=TaskStatus.PENDING,
            created_at=datetime(
                2026,
                7,
                29,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )


def test_task_model_requires_traceable_manual_origin() -> None:
    with pytest.raises(ValueError, match="origin_ref_id"):
        Task(
            task_id="TASK-MANUAL-MISSING-REF",
            internal_sku="SKU-1",
            platform_name="platform",
            action_type=TaskActionType.MANUAL_REVIEW,
            priority=10,
            task_status=TaskStatus.PENDING,
            created_at=datetime(
                2026,
                7,
                29,
                12,
                0,
                tzinfo=timezone.utc,
            ),
            origin_type=TaskOriginType.MANUAL,
        )


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
        for origin_type in ("MANUAL", "AUTOMATION"):
            with pytest.raises(sqlite3.IntegrityError, match="origin_ref_id"):
                connection.execute(
                    """
                    INSERT INTO tasks(
                        task_id, scope_type, scope_key, action_type,
                        priority, task_status, created_at, updated_at,
                        origin_type, origin_ref_id
                    ) VALUES (
                        ?, 'sku', 'SKU-1', 'manual_review',
                        10, 'pending', ?, ?, ?, NULL
                    )
                    """,
                    (
                        f"TASK-{origin_type}-NO-REF",
                        now,
                        now,
                        origin_type,
                    ),
                )


def test_v14_task_origin_is_immutable_after_creation(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    task = Task(
        task_id="TASK-IMMUTABLE-ORIGIN",
        internal_sku="SKU-1",
        platform_name="platform",
        action_type=TaskActionType.MANUAL_REVIEW,
        priority=10,
        task_status=TaskStatus.PENDING,
        created_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        origin_type=TaskOriginType.MANUAL,
        origin_ref_id="web:request-1",
    )
    assert repository.insert_task(task) == 1

    with closing(repository.connect_write()) as connection:
        for statement, parameters in (
            (
                """
                UPDATE tasks
                SET origin_ref_id = ?
                WHERE task_id = ?
                """,
                ("cli:run-2", task.task_id),
            ),
            (
                """
                UPDATE tasks
                SET origin_type = ?, origin_ref_id = ?
                WHERE task_id = ?
                """,
                (
                    TaskOriginType.SYSTEM_EMERGENCY.value,
                    "emergency:forged",
                    task.task_id,
                ),
            ),
            (
                """
                UPDATE tasks
                SET origin_type = ?, origin_ref_id = NULL
                WHERE task_id = ?
                """,
                (TaskOriginType.LEGACY.value, task.task_id),
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(statement, parameters)
            connection.rollback()

        connection.execute(
            """
            UPDATE tasks
            SET result_message = 'allowed non-origin update'
            WHERE task_id = ?
            """,
            (task.task_id,),
        )
        connection.commit()
        row = connection.execute(
            """
            SELECT origin_type, origin_ref_id, result_message
            FROM tasks
            WHERE task_id = ?
            """,
            (task.task_id,),
        ).fetchone()

    assert tuple(row) == (
        TaskOriginType.MANUAL.value,
        "web:request-1",
        "allowed non-origin update",
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
        origin_ref_id="automation-run:test",
    )
    task.origin_ref_id = None

    with pytest.raises(ValueError, match="origin_ref_id"):
        repository.insert_task(task)


@pytest.mark.parametrize(
    "run_status",
    [
        AutomationRunStatus.SUCCESS.value,
        AutomationRunStatus.MERGED.value,
        AutomationRunStatus.SKIPPED.value,
    ],
)
def test_v14_automation_run_accepts_frozen_operational_statuses(
    tmp_path: Path,
    run_status: str,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        _insert_automation_job(connection)
        _insert_automation_run(connection, run_status=run_status)


def test_v14_automation_run_rejects_legacy_succeeded_status(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        _insert_automation_job(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_automation_run(connection, run_status="SUCCEEDED")


def test_v14_incident_category_status_and_resolution_are_frozen(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    now = "2026-07-29T12:00:00+00:00"
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO operational_incidents(
                incident_id, dedupe_key, category,
                source_type, severity, incident_status,
                subject_type, subject_key, title,
                first_detected_at, last_detected_at,
                created_at, updated_at
            ) VALUES (
                'INCIDENT-VALID', 'incident-valid', ?,
                'SCAN', 'S2', ?,
                'PLATFORM', 'platform', 'valid incident',
                ?, ?, ?, ?
            )
            """,
            (
                IncidentCategory.SCAN_INCOMPLETE.value,
                IncidentStatus.OPEN.value,
                now,
                now,
                now,
                now,
            ),
        )
        invalid_cases = (
            ("INCIDENT-BAD-CATEGORY", "UNKNOWN", "OPEN", None),
            (
                "INCIDENT-BAD-STATUS",
                IncidentCategory.SCAN_INCOMPLETE.value,
                "DONE",
                None,
            ),
            (
                "INCIDENT-RESOLVED-WITHOUT-TIME",
                IncidentCategory.SCAN_INCOMPLETE.value,
                IncidentStatus.RESOLVED.value,
                None,
            ),
            (
                "INCIDENT-OPEN-WITH-TIME",
                IncidentCategory.SCAN_INCOMPLETE.value,
                IncidentStatus.OPEN.value,
                now,
            ),
        )
        for incident_id, category, status, resolved_at in invalid_cases:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO operational_incidents(
                        incident_id, dedupe_key, category,
                        source_type, severity, incident_status,
                        subject_type, subject_key, title,
                        first_detected_at, last_detected_at, resolved_at,
                        created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, 'SCAN', 'S2', ?,
                        'PLATFORM', 'platform', 'invalid incident',
                        ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        incident_id,
                        incident_id.lower(),
                        category,
                        status,
                        now,
                        now,
                        resolved_at,
                        now,
                        now,
                    ),
                )


def test_v14_time_policy_is_immutable_and_successors_are_adjacent(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    now = "2026-07-29T12:00:00+00:00"
    with closing(repository.connect_write()) as connection, connection:
        for statement in (
            """
            UPDATE operational_time_policies
            SET platform_cutoff_local_time = '17:00:00'
            WHERE policy_version = 'CN_SINGLE_PLATFORM_2026_V1'
            """,
            """
            UPDATE operational_time_policies
            SET effective_from = '2025-12-31T17:00:00+00:00'
            WHERE policy_version = 'CN_SINGLE_PLATFORM_2026_V1'
            """,
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(statement)
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            connection.execute(
                """
                DELETE FROM operational_time_policies
                WHERE policy_version = 'CN_SINGLE_PLATFORM_2026_V1'
                """
            )
        connection.execute(
            """
            UPDATE operational_time_policies
            SET effective_to = '2026-07-29T10:00:00+00:00'
            WHERE policy_version = 'CN_SINGLE_PLATFORM_2026_V1'
            """
        )
        _insert_time_policy(
            connection,
            version="V2",
            effective_from="2026-07-29T10:00:00+00:00",
            effective_to=None,
            created_at=now,
            supersedes="CN_SINGLE_PLATFORM_2026_V1",
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                UPDATE operational_time_policies
                SET effective_to = NULL
                WHERE policy_version = 'CN_SINGLE_PLATFORM_2026_V1'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                UPDATE operational_time_policies
                SET effective_to = '2026-07-29T11:00:00+00:00'
                WHERE policy_version = 'CN_SINGLE_PLATFORM_2026_V1'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="adjacent"):
            _insert_time_policy(
                connection,
                version="GAP",
                effective_from="2026-07-29T11:00:00+00:00",
                effective_to=None,
                created_at=now,
                supersedes="CN_SINGLE_PLATFORM_2026_V1",
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_time_policy(
                connection,
                version="NON-UTC",
                effective_from="2026-07-30T00:00:00+08:00",
                effective_to="2026-07-31T00:00:00+08:00",
                created_at=now,
                supersedes="CN_SINGLE_PLATFORM_2026_V1",
            )


def test_v14_time_policy_rejects_overlap_after_adjacent_chain(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    now = "2026-07-29T12:00:00+00:00"
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            UPDATE operational_time_policies
            SET effective_to = '2026-07-29T10:00:00+00:00'
            WHERE policy_version = 'CN_SINGLE_PLATFORM_2026_V1'
            """
        )
        _insert_time_policy(
            connection,
            version="V2",
            effective_from="2026-07-29T10:00:00+00:00",
            effective_to="2026-07-29T20:00:00+00:00",
            created_at=now,
            supersedes="CN_SINGLE_PLATFORM_2026_V1",
        )
        _insert_time_policy(
            connection,
            version="V3",
            effective_from="2026-07-29T20:00:00+00:00",
            effective_to=None,
            created_at=now,
            supersedes="V2",
        )
        with pytest.raises(sqlite3.IntegrityError, match="must not overlap"):
            _insert_time_policy(
                connection,
                version="OVERLAP",
                effective_from="2026-07-29T10:00:00+00:00",
                effective_to="2026-07-29T11:00:00+00:00",
                created_at=now,
                supersedes="CN_SINGLE_PLATFORM_2026_V1",
            )


def test_v14_time_policy_replacement_is_atomic_and_healthy(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    effective_from = datetime(
        2026,
        7,
        29,
        10,
        0,
        tzinfo=timezone.utc,
    )

    repository.replace_current_operational_time_policy(
        expected_current_policy_version="CN_SINGLE_PLATFORM_2026_V1",
        successor_policy_version="V2",
        effective_from=effective_from,
        platform_cutoff_local_time="18:00:00",
        seller_cutoff_local_time="20:00:00",
        peak_start_local_time="16:00:00",
        created_by="test-harness:policy-replacement",
    )

    with closing(repository.connect_read()) as connection:
        rows = connection.execute(
            """
            SELECT policy_version, effective_from, effective_to,
                   supersedes_policy_version
            FROM operational_time_policies
            ORDER BY effective_from
            """
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        (
            "CN_SINGLE_PLATFORM_2026_V1",
            "2025-12-31T16:00:00+00:00",
            effective_from.isoformat(),
            None,
        ),
        (
            "V2",
            effective_from.isoformat(),
            None,
            "CN_SINGLE_PLATFORM_2026_V1",
        ),
    ]
    assert repository.check_schema_health().ok


def test_v14_time_policy_replacement_rolls_back_on_successor_failure(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(sqlite3.IntegrityError):
        repository.replace_current_operational_time_policy(
            expected_current_policy_version="CN_SINGLE_PLATFORM_2026_V1",
            successor_policy_version="V2",
            effective_from=datetime(
                2026,
                7,
                29,
                10,
                0,
                tzinfo=timezone.utc,
            ),
            platform_cutoff_local_time="21:00:00",
            seller_cutoff_local_time="20:00:00",
            peak_start_local_time="16:00:00",
            created_by="test-harness:policy-failure",
        )

    with closing(repository.connect_read()) as connection:
        rows = connection.execute(
            """
            SELECT policy_version, effective_to
            FROM operational_time_policies
            ORDER BY policy_version
            """
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("CN_SINGLE_PLATFORM_2026_V1", None)
    ]
    assert repository.check_schema_health().ok


def test_v14_concurrent_time_policy_replacement_has_one_winner(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    barrier = Barrier(2)

    def replace(version: str) -> tuple[str, str]:
        barrier.wait()
        try:
            repository.replace_current_operational_time_policy(
                expected_current_policy_version=(
                    "CN_SINGLE_PLATFORM_2026_V1"
                ),
                successor_policy_version=version,
                effective_from=datetime(
                    2026,
                    7,
                    29,
                    10,
                    0,
                    tzinfo=timezone.utc,
                ),
                platform_cutoff_local_time="18:00:00",
                seller_cutoff_local_time="20:00:00",
                peak_start_local_time="16:00:00",
                created_by=f"test-harness:{version}",
            )
        except ValueError as exc:
            return ("rejected", str(exc))
        return ("success", version)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(replace, ("V2-A", "V2-B")))

    successes = [value for status, value in results if status == "success"]
    rejections = [value for status, value in results if status == "rejected"]
    assert len(successes) == 1
    assert len(rejections) == 1
    assert "current operational time policy changed" in rejections[0]

    with closing(repository.connect_read()) as connection:
        current_rows = connection.execute(
            """
            SELECT policy_version
            FROM operational_time_policies
            WHERE effective_to IS NULL
            """
        ).fetchall()
    assert [str(row["policy_version"]) for row in current_rows] == successes
    assert repository.check_schema_health().ok


def test_v14_observation_and_summary_fact_tables_are_append_only(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        _insert_append_only_fixture(connection)
        identities = (
            (
                "product_observation_batches",
                "observation_batch_id",
                "PRODUCT-BATCH-1",
            ),
            (
                "product_observation_items",
                "observation_item_id",
                "PRODUCT-ITEM-1",
            ),
            (
                "order_observation_batches",
                "observation_batch_id",
                "ORDER-BATCH-1",
            ),
            (
                "order_observation_items",
                "observation_item_id",
                "ORDER-ITEM-1",
            ),
            (
                "sales_estimate_segments",
                "estimate_segment_id",
                "ESTIMATE-1",
            ),
            (
                "platform_trade_day_summary_events",
                "event_id",
                "SUMMARY-EVENT-1",
            ),
            (
                "platform_trade_day_summary_inputs",
                "summary_id",
                "SUMMARY-APPEND-ONLY",
            ),
        )
        for table_name, identity_column, identity_value in identities:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                connection.execute(
                    f"""
                    UPDATE {table_name}
                    SET {identity_column} = {identity_column}
                    WHERE {identity_column} = ?
                    """,
                    (identity_value,),
                )
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                connection.execute(
                    f"""
                    DELETE FROM {table_name}
                    WHERE {identity_column} = ?
                    """,
                    (identity_value,),
                )


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
                    transaction_amount_total = '10.00'
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


def test_v14_health_detects_missing_append_only_trigger(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    trigger_name = (
        "trg_order_observation_items_append_only_update"
    )
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(f"DROP TRIGGER {trigger_name}")

    health = repository.check_schema_health()

    assert not health.ok
    assert any(
        trigger_name in error for error in health.constraint_errors
    )


def test_v14_replaces_empty_provisional_order_shape_in_place(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            ALTER TABLE order_observation_items
            ADD COLUMN seller_received_amount TEXT
            """
        )

    repository.init_schema()

    with closing(repository.connect_read()) as connection:
        item_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(order_observation_items)"
            )
        }
        summary_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(platform_trade_day_summaries)"
            )
        }
    assert "order_transaction_amount" in item_columns
    assert "seller_received_amount" not in item_columns
    assert "transaction_amount_total" in summary_columns
    assert repository.check_schema_health().ok


def test_v14_refuses_to_guess_nonempty_provisional_order_facts(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    with closing(repository.connect_write()) as connection, connection:
        _insert_append_only_fixture(connection)
        connection.execute(
            """
            ALTER TABLE order_observation_items
            ADD COLUMN seller_received_amount TEXT
            """
        )

    with pytest.raises(RuntimeError, match="cannot be guessed"):
        repository.init_schema()

    with closing(repository.connect_read()) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM order_observation_batches"
        ).fetchone()[0] == 1
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(order_observation_items)"
            )
        }
    assert "seller_received_amount" in columns


def test_v14_health_detects_missing_task_origin_immutable_trigger(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    trigger_name = "trg_tasks_origin_immutable"
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(f"DROP TRIGGER {trigger_name}")

    health = repository.check_schema_health()

    assert not health.ok
    assert any(
        trigger_name in error for error in health.constraint_errors
    )


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
                    created_at, updated_at, origin_type, origin_ref_id,
                    platform_trade_date, seller_operation_date
                ) VALUES (
                    'TASK-HISTORICAL', '2026-07-28', 'sku', 'SKU-1',
                    'manual_review', 10, 'pending',
                    ?, ?, 'MANUAL', 'test-harness:v13-migration',
                    '2099-01-01', '2099-01-02'
                )
            """,
            (now, now),
        )
    _downgrade_fixture_to_v13(repository)

    repository.init_schema()

    assert repository.schema_versions() == list(range(1, 17))
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


def test_existing_v14_summary_inputs_gain_manifest_dimension(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    now = "2026-07-29T12:00:00+00:00"
    with closing(repository.connect_write()) as connection, connection:
        connection.execute(
            """
            DROP TRIGGER
            trg_platform_trade_day_summary_inputs_append_only_update
            """
        )
        connection.execute(
            """
            DROP TRIGGER
            trg_platform_trade_day_summary_inputs_append_only_delete
            """
        )
        connection.execute(
            "DROP INDEX ix_trade_day_summary_inputs_ref"
        )
        connection.execute(
            "DROP TABLE platform_trade_day_summary_inputs"
        )
        connection.execute(
            """
            CREATE TABLE platform_trade_day_summary_inputs (
                summary_id TEXT NOT NULL,
                input_type TEXT NOT NULL,
                input_ref_id TEXT NOT NULL,
                input_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(summary_id, input_type, input_ref_id),
                FOREIGN KEY(summary_id)
                    REFERENCES platform_trade_day_summaries(summary_id)
            )
            """
        )
        _insert_summary(
            connection,
            summary_id="SUMMARY-OLD-V14",
            series_id="SERIES-OLD-V14",
            fact_source=None,
            quality_level="UNAVAILABLE",
            summary_status="PROVISIONAL",
            sold_qty=None,
            order_count=None,
            amount=None,
            finalized_at=None,
        )
        connection.execute(
            """
            INSERT INTO platform_trade_day_summary_inputs(
                summary_id, input_type, input_ref_id,
                input_sha256, created_at
            ) VALUES (
                'SUMMARY-OLD-V14', 'PRODUCT_BATCH',
                'PRODUCT-BATCH-OLD', 'sha256:old', ?
            )
            """,
            (now,),
        )

    repository.init_schema()

    assert repository.check_schema_health().ok
    with closing(repository.connect_read()) as connection:
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(platform_trade_day_summary_inputs)"
            )
        }
        row = connection.execute(
            """
            SELECT input_manifest_sha256, input_ref_id
            FROM platform_trade_day_summary_inputs
            WHERE summary_id = 'SUMMARY-OLD-V14'
            """
        ).fetchone()
    assert "input_manifest_sha256" in columns
    assert tuple(row) == ("sha256:input", "PRODUCT-BATCH-OLD")


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
            connection.execute(
                "DROP TRIGGER IF EXISTS trg_tasks_traceable_origin_insert"
            )
            connection.execute(
                "DROP TRIGGER IF EXISTS trg_tasks_traceable_origin_update"
            )
            connection.execute(
                "DROP TRIGGER IF EXISTS trg_tasks_origin_immutable"
            )
            for column in V14_TASK_COLUMNS:
                connection.execute(
                    f"ALTER TABLE tasks DROP COLUMN {column}"
                )
            connection.execute(
                "DELETE FROM runtime_schema_migrations WHERE schema_version >= 14"
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
            sold_qty, order_count, transaction_amount_total,
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


def _insert_automation_job(connection) -> None:
    now = "2026-07-29T12:00:00+00:00"
    connection.execute(
        """
        INSERT OR IGNORE INTO automation_jobs(
            job_id, job_type, display_name,
            schedule_kind, schedule_expression,
            created_at, updated_at
        ) VALUES (
            'JOB-1', 'ONLINE_PULSE', 'Online pulse',
            'INTERVAL', '10m', ?, ?
        )
        """,
        (now, now),
    )


def _insert_automation_run(connection, *, run_status: str) -> None:
    now = "2026-07-29T12:00:00+00:00"
    connection.execute(
        """
        INSERT INTO automation_runs(
            run_id, job_id, job_type, logical_run_key, run_status,
            platform_name, platform_trade_date,
            seller_operation_date, seller_phase,
            time_policy_version, scheduled_for,
            created_at, updated_at
        ) VALUES (
            ?, 'JOB-1', 'ONLINE_PULSE', ?, ?,
            'platform', '2026-07-29',
            '2026-07-29', 'NORMAL_SALES',
            'CN_SINGLE_PLATFORM_2026_V1', ?,
            ?, ?
        )
        """,
        (
            f"RUN-{run_status}",
            f"logical-{run_status}",
            run_status,
            now,
            now,
            now,
        ),
    )


def _insert_time_policy(
    connection,
    *,
    version: str,
    effective_from: str,
    effective_to: str | None,
    created_at: str,
    supersedes: str,
) -> None:
    connection.execute(
        """
        INSERT INTO operational_time_policies(
            policy_version, timezone_name,
            platform_cutoff_local_time,
            seller_cutoff_local_time,
            peak_start_local_time,
            effective_from, effective_to,
            created_at, created_by,
            supersedes_policy_version
        ) VALUES (
            ?, 'Asia/Shanghai',
            '18:00:00', '20:00:00', '16:00:00',
            ?, ?, ?, 'test', ?
        )
        """,
        (
            version,
            effective_from,
            effective_to,
            created_at,
            supersedes,
        ),
    )


def _insert_append_only_fixture(connection) -> None:
    now = "2026-07-29T12:00:00+00:00"
    _insert_automation_job(connection)
    _insert_automation_run(connection, run_status="RUNNING")
    connection.execute(
        """
        INSERT INTO product_observation_batches(
            observation_batch_id, automation_run_id,
            platform_name, scan_type, batch_status,
            scan_started_at, scan_completed_at,
            requested_scope_json, scope_complete,
            end_marker_verified, content_sha256,
            time_policy_version, created_at
        ) VALUES (
            'PRODUCT-BATCH-1', 'RUN-RUNNING',
            'platform', 'ONLINE_PULSE', 'ACCEPTED',
            ?, ?, '{}', 1, 1, 'sha256:product-batch',
            'CN_SINGLE_PLATFORM_2026_V1', ?
        )
        """,
        (now, now, now),
    )
    connection.execute(
        """
        INSERT INTO product_observation_items(
            observation_item_id, observation_batch_id,
            internal_sku, platform_product_name, grade,
            observed_online, observed_at,
            platform_trade_date, seller_operation_date,
            seller_phase, page_identity_key, mapping_status
        ) VALUES (
            'PRODUCT-ITEM-1', 'PRODUCT-BATCH-1',
            'SKU-1', 'Rose', 'A',
            1, ?, '2026-07-29', '2026-07-29',
            'NORMAL_SALES', 'page:rose:a', 'VERIFIED'
        )
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO order_observation_batches(
            observation_batch_id, automation_run_id,
            platform_name, requested_platform_trade_date,
            trade_day_status, capability_result, batch_status,
            scan_started_at, scan_completed_at,
            requested_range_json, scope_complete,
            end_marker_verified, content_sha256,
            time_policy_version, created_at
        ) VALUES (
            'ORDER-BATCH-1', 'RUN-RUNNING',
            'platform', '2026-07-29', 'CLOSED',
            'SUCCEEDED', 'ACCEPTED',
            ?, ?, '{}', 1, 1, 'sha256:order-batch',
            'CN_SINGLE_PLATFORM_2026_V1', ?
        )
        """,
        (now, now, now),
    )
    connection.execute(
        """
        INSERT INTO order_observation_items(
            observation_item_id, observation_batch_id,
            platform_name, platform_trade_date, trade_day_status,
            order_identity_fingerprint, occurrence_no,
            order_created_at, platform_product_name,
            grade, internal_sku, mapping_status,
            order_qty, order_transaction_amount,
            observed_at, seller_operation_date, seller_phase,
            raw_observation_sha256
        ) VALUES (
            'ORDER-ITEM-1', 'ORDER-BATCH-1',
            'platform', '2026-07-29', 'CLOSED',
            'sha256:order-fingerprint', 1,
            ?, 'Rose', 'A', 'SKU-1', 'VERIFIED',
            1, '10', ?, '2026-07-29', 'NORMAL_SALES',
            'sha256:order-item'
        )
        """,
        (now, now),
    )
    connection.execute(
        """
        INSERT INTO sales_estimate_segments(
            estimate_segment_id, platform_name,
            internal_sku, platform_trade_date,
            interval_started_at, interval_ended_at,
            inventory_before, inventory_after,
            estimated_sold_qty, estimation_eligible,
            estimation_reason, quality_level,
            mapping_version, supporting_observation_ids_json,
            algorithm_version, created_at
        ) VALUES (
            'ESTIMATE-1', 'platform',
            'SKU-1', '2026-07-29',
            '2026-07-29T11:00:00+00:00',
            '2026-07-29T12:00:00+00:00',
            10, 9, 1, 1,
            'complete interval', 'SCAN_ESTIMATED_HIGH',
            'mapping-v1', '["PRODUCT-ITEM-1"]',
            'estimate-v1', ?
        )
        """,
        (now,),
    )
    _insert_summary(
        connection,
        summary_id="SUMMARY-APPEND-ONLY",
        series_id="SERIES-APPEND-ONLY",
        fact_source=None,
        quality_level="UNAVAILABLE",
        summary_status="PROVISIONAL",
        sold_qty=None,
        order_count=None,
        amount=None,
        finalized_at=None,
    )
    connection.execute(
        """
        INSERT INTO platform_trade_day_summary_events(
            event_id, summary_id, to_status,
            trigger_type, quality_level_after,
            input_manifest_sha256, changed_at, changed_by
        ) VALUES (
            'SUMMARY-EVENT-1', 'SUMMARY-APPEND-ONLY', 'PROVISIONAL',
            'TEST', 'UNAVAILABLE',
            'sha256:input', ?, 'test'
        )
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO platform_trade_day_summary_inputs(
            summary_id, input_manifest_sha256,
            input_type, input_ref_id, input_sha256, created_at
        ) VALUES (
            'SUMMARY-APPEND-ONLY', 'sha256:input',
            'PRODUCT_BATCH', 'PRODUCT-BATCH-1',
            'sha256:product-batch', ?
        )
        """,
        (now,),
    )

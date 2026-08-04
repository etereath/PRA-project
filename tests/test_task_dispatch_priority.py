from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.enums import TaskActionType, TaskOriginType, TaskStatus
from app.exceptions import ValidationError
from app.models import Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.task_dispatch_priority import (
    assert_selected_tasks_have_dispatch_priority,
    has_pending_urgent_incident_task,
)


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def _task(
    task_id: str,
    *,
    origin_type: TaskOriginType,
    origin_ref_id: str,
    priority: int,
    action_type: TaskActionType = TaskActionType.SET_OFFLINE,
) -> Task:
    return Task(
        task_id=task_id,
        internal_sku="SKU-1",
        platform_name="platform",
        action_type=action_type,
        priority=priority,
        task_status=TaskStatus.PENDING,
        created_at=NOW + timedelta(seconds=priority),
        target_status=("offline" if action_type is TaskActionType.SET_OFFLINE else None),
        expected_old_price=(10 if action_type is TaskActionType.UPDATE_PRICE else None),
        target_price=(12 if action_type is TaskActionType.UPDATE_PRICE else None),
        origin_type=origin_type,
        origin_ref_id=origin_ref_id,
        expires_at=NOW + timedelta(hours=1),
    )


def _repository(tmp_path) -> SQLiteRuntimeRepository:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    repository.init_schema()
    normal = _task(
        "TASK-NORMAL",
        origin_type=TaskOriginType.AUTOMATION,
        origin_ref_id="rule:normal",
        priority=0,
    )
    emergency = _task(
        "TASK-EMERGENCY",
        origin_type=TaskOriginType.SYSTEM_EMERGENCY,
        origin_ref_id="emergency:incident-1",
        priority=1,
    )
    human = _task(
        "TASK-HUMAN",
        origin_type=TaskOriginType.MANUAL,
        origin_ref_id="incident-review:review-1",
        priority=0,
    )
    with repository.connect_write() as connection, connection:
        SQLiteRuntimeRepository._insert_tasks_on_connection(
            connection,
            [normal, emergency, human],
        )
    return repository


def test_incident_human_then_system_emergency_precede_normal_pending_tasks(
    tmp_path,
) -> None:
    repository = _repository(tmp_path)

    assert [task.task_id for task in repository.list_tasks()] == [
        "TASK-HUMAN",
        "TASK-EMERGENCY",
        "TASK-NORMAL",
    ]


def test_explicit_dispatch_cannot_bypass_incident_priority(tmp_path) -> None:
    repository = _repository(tmp_path)
    with repository.connect_read() as connection:
        with pytest.raises(ValidationError, match="must dispatch first"):
            assert_selected_tasks_have_dispatch_priority(
                connection,
                selected_task_ids=["TASK-NORMAL"],
                platform_name="platform",
            )
        with pytest.raises(ValidationError, match="must dispatch first"):
            assert_selected_tasks_have_dispatch_priority(
                connection,
                selected_task_ids=["TASK-EMERGENCY"],
                platform_name="platform",
            )
        assert_selected_tasks_have_dispatch_priority(
            connection,
            selected_task_ids=["TASK-HUMAN"],
            platform_name="platform",
        )


def test_pending_incident_action_defers_new_ui_work(tmp_path) -> None:
    repository = _repository(tmp_path)
    with repository.connect_read() as connection:
        assert has_pending_urgent_incident_task(connection)

    with repository.connect_write() as connection, connection:
        connection.execute(
            "UPDATE tasks SET task_status = 'success' "
            "WHERE task_id IN ('TASK-HUMAN', 'TASK-EMERGENCY')"
        )
    with repository.connect_read() as connection:
        assert not has_pending_urgent_incident_task(connection)

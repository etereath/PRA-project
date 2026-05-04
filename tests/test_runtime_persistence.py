from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from app.enums import NotificationSendStatus, ReviewTaskStatus, TaskActionType, TaskStatus
from app.models import NotificationLog, Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.runtime import NotificationLogService, ReviewTaskService, RuntimeTaskService


def _runtime_task(task_id: str, *, status: TaskStatus = TaskStatus.PENDING) -> Task:
    return Task(
        task_id=task_id,
        internal_sku=None,
        platform_name=None,
        action_type=TaskActionType.CAPACITY_WARNING,
        priority=2,
        task_status=status,
        created_at=datetime(2026, 5, 4, 9, 0),
        trade_date=date(2026, 5, 4),
        scope_type="global",
        scope_key="2026-05-04",
        dedupe_key="2026-05-04|global|2026-05-04|capacity_warning",
        decision_trace={"reason": "capacity"},
    )


class RuntimePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "runtime.sqlite3"
        self.repository = SQLiteRuntimeRepository(self.db_path)
        self.task_service = RuntimeTaskService(self.repository)
        self.task_service.init_schema()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_schema_initializes_version_and_partial_unique_index(self) -> None:
        self.task_service.init_schema()
        self.assertEqual(self.repository.schema_versions(), [1])
        connection = sqlite3.connect(self.db_path)
        try:
            indexes = connection.execute("PRAGMA index_list(tasks)").fetchall()
        finally:
            connection.close()
        self.assertTrue(any(row[1] == "ux_tasks_open_dedupe" and row[4] for row in indexes))

    def test_dedupe_key_only_blocks_open_tasks(self) -> None:
        first = _runtime_task("TASK-1")
        duplicate = _runtime_task("TASK-2")
        self.assertEqual(self.task_service.create_tasks([first]), 1)
        self.assertEqual(self.task_service.create_tasks([duplicate]), 0)

        self.task_service.change_status(
            task_id="TASK-1",
            to_status=TaskStatus.CANCELLED,
            changed_by="test",
            reason="close old task",
        )
        self.assertEqual(self.task_service.create_tasks([duplicate]), 1)
        self.assertEqual(len(self.task_service.list_tasks()), 2)

    def test_status_transition_records_history_and_rejects_invalid_transition(self) -> None:
        self.task_service.create_tasks([_runtime_task("TASK-1")])
        self.task_service.change_status(
            task_id="TASK-1",
            to_status=TaskStatus.RUNNING,
            changed_by="worker",
            reason="start",
            metadata={"batch": "B1"},
        )
        history = self.task_service.list_status_history("TASK-1")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].from_status, TaskStatus.PENDING)
        self.assertEqual(history[0].to_status, TaskStatus.RUNNING)
        self.assertEqual(history[0].metadata["batch"], "B1")

        with self.assertRaises(Exception):
            self.task_service.change_status(
                task_id="TASK-1",
                to_status=TaskStatus.PENDING,
                changed_by="worker",
                reason="invalid",
            )

    def test_review_task_can_resolve_and_update_source_through_runtime_service(self) -> None:
        source = _runtime_task("TASK-1", status=TaskStatus.MANUAL_REVIEW)
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        self.assertEqual(review_service.create_from_tasks([source]), 1)

        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]
        resolved = review_service.resolve_review_task(
            review_task_id=review.review_task_id,
            status=ReviewTaskStatus.REJECTED,
            actor="alice",
            note="not enough capacity",
            source_task_status=TaskStatus.CANCELLED,
        )
        self.assertEqual(resolved.review_status, ReviewTaskStatus.REJECTED)
        task = self.task_service.list_tasks()[0]
        self.assertEqual(task.task_status, TaskStatus.CANCELLED)

    def test_notification_log_dedupes_by_dedupe_key(self) -> None:
        service = NotificationLogService(self.repository)
        log = NotificationLog(
            notification_id="N-1",
            related_task_id=None,
            related_review_task_id=None,
            recipient_type="role",
            recipient="operator",
            channel="mock",
            sent_at=datetime(2026, 5, 4, 10, 0),
            send_status=NotificationSendStatus.SUCCESS.value,
            dedupe_key="notice|2026-05-04|operator",
            message="capacity warning",
        )
        duplicate = NotificationLog(
            notification_id="N-2",
            related_task_id=None,
            related_review_task_id=None,
            recipient_type="role",
            recipient="operator",
            channel="mock",
            sent_at=datetime(2026, 5, 4, 10, 1),
            send_status=NotificationSendStatus.SUCCESS.value,
            dedupe_key="notice|2026-05-04|operator",
            message="capacity warning again",
        )
        self.assertEqual(service.append(log), 1)
        self.assertEqual(self.repository.insert_notification_logs([duplicate]), 0)
        self.assertEqual(len(service.list_logs()), 1)


if __name__ == "__main__":
    unittest.main()

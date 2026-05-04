from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from app.enums import NotificationSendStatus, ReviewTaskStatus, TaskActionType, TaskStatus
from app.models import NotificationLog, Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.runtime import (
    MockNotificationSender,
    NotificationLogService,
    ReviewNotificationService,
    ReviewTaskService,
    RuntimeTaskService,
)


def _runtime_task(
    task_id: str,
    *,
    status: TaskStatus = TaskStatus.PENDING,
    action_type: TaskActionType = TaskActionType.CAPACITY_WARNING,
    required_by: datetime | None = None,
) -> Task:
    return Task(
        task_id=task_id,
        internal_sku=None,
        platform_name=None,
        action_type=action_type,
        priority=2,
        task_status=status,
        created_at=datetime(2026, 5, 4, 9, 0),
        trade_date=date(2026, 5, 4),
        scope_type="global",
        scope_key="2026-05-04",
        dedupe_key=f"2026-05-04|global|2026-05-04|{action_type.value}|{task_id}",
        decision_trace={"reason": "runtime"},
        result_message="needs manual review",
        required_by=required_by,
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
        duplicate.dedupe_key = first.dedupe_key
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

    def test_mock_notification_sender_returns_structured_result(self) -> None:
        sender = MockNotificationSender()
        result = sender.send(
            NotificationLog(
                notification_id="N-1",
                related_task_id=None,
                related_review_task_id=None,
                recipient_type="role",
                recipient="operations",
                channel="mock",
                sent_at=None,
                send_status=NotificationSendStatus.PENDING.value,
                dedupe_key="mock|1",
                message="mock message",
            )
        )
        self.assertEqual(result.send_status, NotificationSendStatus.SUCCESS.value)
        self.assertIsNotNone(result.sent_at)
        self.assertEqual(result.raw_response_json, {"mock": True})

    def test_review_task_creation_also_creates_initial_notification_log(self) -> None:
        source = _runtime_task("TASK-1")
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)

        with patch.dict(
            "os.environ",
            {
                "DEFAULT_NOTIFICATION_RECIPIENT_TYPE": "role",
                "DEFAULT_NOTIFICATION_RECIPIENT": "operations",
                "DEFAULT_NOTIFICATION_CHANNEL": "mock",
            },
            clear=False,
        ):
            summary = review_service.create_from_tasks([source])

        self.assertEqual(summary.inserted_review_tasks_count, 1)
        self.assertEqual(summary.inserted_notification_logs_count, 1)
        self.assertEqual(summary.notification_errors, [])
        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]
        logs = NotificationLogService(self.repository).list_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].related_review_task_id, review.review_task_id)
        self.assertEqual(logs[0].related_task_id, source.task_id)
        self.assertEqual(logs[0].send_status, NotificationSendStatus.SUCCESS.value)
        self.assertIsNotNone(logs[0].sent_at)
        self.assertIn(review.review_type, logs[0].message)
        self.assertNotIn("decision_trace", logs[0].message)

    def test_notification_failure_does_not_block_review_creation(self) -> None:
        class FailingReviewNotificationService(ReviewNotificationService):
            def create_initial_notification(self, review_task):
                raise RuntimeError("mock notification failure")

        source = _runtime_task("TASK-1")
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(
            self.repository,
            runtime_task_service=self.task_service,
            notification_service=FailingReviewNotificationService(self.repository),
        )
        summary = review_service.create_from_tasks([source])
        self.assertEqual(summary.inserted_review_tasks_count, 1)
        self.assertEqual(summary.inserted_notification_logs_count, 0)
        self.assertEqual(len(summary.notification_errors), 1)
        self.assertEqual(len(review_service.list_review_tasks()), 1)
        self.assertEqual(len(NotificationLogService(self.repository).list_logs()), 0)

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

    def test_review_task_can_resolve_and_update_source_through_runtime_service(self) -> None:
        source = _runtime_task("TASK-1", status=TaskStatus.MANUAL_REVIEW)
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        summary = review_service.create_from_tasks([source])
        self.assertEqual(summary.inserted_review_tasks_count, 1)

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

    def test_review_task_requires_pending_and_writes_metadata_to_history(self) -> None:
        source = _runtime_task("TASK-1", status=TaskStatus.MANUAL_REVIEW)
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        review_service.create_from_tasks([source])

        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]
        review_service.resolve_review_task(
            review_task_id=review.review_task_id,
            status=ReviewTaskStatus.APPROVED,
            actor="alice",
            actor_source="session_user",
            note="approved in web",
            resolution_payload={"reviewer_code": "R-001", "adjustment": {"target_price": "8.8"}},
            source_task_status=TaskStatus.PENDING,
        )
        history = self.task_service.list_status_history("TASK-1")
        self.assertEqual(history[-1].metadata["review_task_id"], review.review_task_id)
        self.assertEqual(history[-1].metadata["actor_source"], "session_user")
        self.assertTrue(history[-1].metadata["resolution_payload_summary"]["reviewer_code_present"])

        with self.assertRaises(Exception):
            review_service.resolve_review_task(
                review_task_id=review.review_task_id,
                status=ReviewTaskStatus.REJECTED,
                actor="bob",
            )

    def test_expire_pending_review_tasks_supports_dry_run_and_apply(self) -> None:
        source = _runtime_task(
            "TASK-1",
            status=TaskStatus.MANUAL_REVIEW,
            action_type=TaskActionType.LABOR_REQUIRED,
            required_by=datetime(2026, 5, 4, 8, 0),
        )
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        review_service.create_from_tasks([source])
        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]

        dry_run = review_service.expire_pending_review_tasks(
            now=datetime(2026, 5, 4, 9, 30),
            apply=False,
        )
        self.assertEqual(dry_run.scanned_review_tasks, 1)
        self.assertEqual(dry_run.expired_review_tasks, 1)
        self.assertEqual(review_service.get_review_task(review.review_task_id).review_status, ReviewTaskStatus.PENDING)
        self.assertEqual(self.task_service.get_task(source.task_id).task_status, TaskStatus.MANUAL_REVIEW)

        applied = review_service.expire_pending_review_tasks(
            now=datetime(2026, 5, 4, 9, 30),
            apply=True,
        )
        self.assertEqual(applied.expired_review_tasks, 1)
        self.assertEqual(applied.expired_source_tasks, 1)
        updated_review = review_service.get_review_task(review.review_task_id)
        updated_task = self.task_service.get_task(source.task_id)
        self.assertEqual(updated_review.review_status, ReviewTaskStatus.EXPIRED)
        self.assertEqual(updated_task.task_status, TaskStatus.EXPIRED)
        history = self.task_service.list_status_history(source.task_id)
        self.assertEqual(history[-1].metadata["timeout_policy"], "uniform_conservative_v1")
        self.assertTrue(history[-1].metadata["fallback_to_safe_default"])
        self.assertEqual(history[-1].metadata["confirmed_temp_worker_count"], 0)
        self.assertEqual(history[-1].metadata["confirmed_packing_capacity_qty"], 250)

    def test_expire_pending_review_tasks_skips_non_manual_review_source(self) -> None:
        source = _runtime_task(
            "TASK-1",
            status=TaskStatus.PENDING,
            action_type=TaskActionType.CAPACITY_WARNING,
            required_by=datetime(2026, 5, 4, 8, 0),
        )
        self.task_service.create_tasks([source])
        review_service = ReviewTaskService(self.repository, runtime_task_service=self.task_service)
        review_service.create_from_tasks([source])

        summary = review_service.expire_pending_review_tasks(
            now=datetime(2026, 5, 4, 9, 0),
            apply=True,
        )
        self.assertEqual(summary.expired_review_tasks, 1)
        self.assertEqual(summary.expired_source_tasks, 0)
        self.assertEqual(summary.skipped_source_tasks, 1)
        self.assertEqual(self.task_service.get_task(source.task_id).task_status, TaskStatus.PENDING)


if __name__ == "__main__":
    unittest.main()

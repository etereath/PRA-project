from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.cli import main
from app.enums import (
    NotificationOutboxStatus,
    ReviewTaskStatus,
    TaskActionType,
    TaskOriginType,
    TaskStatus,
)
from app.models import ReviewTask, Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.runtime import ReviewTaskService, RuntimeTaskService


def _runtime_task(task_id: str, *, status: TaskStatus = TaskStatus.MANUAL_REVIEW, required_by: datetime | None = None) -> Task:
    return Task(
        task_id=task_id,
        internal_sku=None,
        platform_name=None,
        action_type=TaskActionType.LABOR_REQUIRED,
        priority=2,
        task_status=status,
        created_at=datetime(2026, 5, 4, 9, 0),
        origin_type=TaskOriginType.MANUAL,
        origin_ref_id=f"test-harness:test-cli:{task_id}",
        trade_date=datetime(2026, 5, 4).date(),
        scope_type="global",
        scope_key="2026-05-04",
        dedupe_key=f"2026-05-04|global|2026-05-04|labor_required|{task_id}",
        decision_trace={"reason": "cli"},
        result_message="needs manual review",
        required_by=required_by,
    )


class CliTests(unittest.TestCase):
    def test_notification_worker_rejects_test_channels_outside_dev_mode(self) -> None:
        for channel in ("mock", "fake", "scripted"):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as temp_dir:
                db_path = Path(temp_dir) / "runtime.sqlite3"
                repository = SQLiteRuntimeRepository(db_path)
                repository.init_schema()
                review = ReviewTaskService(repository).notification_service
                with patch.dict(
                    "os.environ",
                    {"DEFAULT_NOTIFICATION_CHANNEL": channel, "DEV_MODE": "false"},
                    clear=False,
                ):
                    created = review.create_review_task_atomically(
                        ReviewTask(
                            review_task_id=f"CLI-{channel}",
                            trade_date=None,
                            scope_type="global",
                            scope_key="cli",
                            dedupe_key=f"cli:{channel}",
                            source_task_id=None,
                            review_type="manual_review",
                            review_status=ReviewTaskStatus.PENDING,
                            created_at=datetime(2026, 7, 17, 10, 0),
                            updated_at=datetime(2026, 7, 17, 10, 0),
                        )
                    )[2]
                    output = io.StringIO()
                    args = ["cli", "notification-worker", "--runtime-db", str(db_path)]
                    with patch.object(sys, "argv", args), redirect_stdout(output):
                        exit_code = main()
                self.assertEqual(exit_code, 2)
                self.assertIn("DEV_MODE=true", output.getvalue())
                self.assertEqual(
                    repository.get_notification_outbox(created.notification_id).status,
                    NotificationOutboxStatus.PENDING.value,
                )
                self.assertEqual(repository.list_notification_delivery_attempts(created.notification_id), [])

    def test_notification_worker_allows_explicit_dev_test_channel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime.sqlite3"
            repository = SQLiteRuntimeRepository(db_path)
            repository.init_schema()
            review = ReviewTaskService(repository).notification_service
            with patch.dict(
                "os.environ",
                {"DEFAULT_NOTIFICATION_CHANNEL": "mock", "DEV_MODE": "true"},
                clear=False,
            ):
                created = review.create_review_task_atomically(
                    ReviewTask(
                        review_task_id="CLI-DEV-MOCK",
                        trade_date=None,
                        scope_type="global",
                        scope_key="cli",
                        dedupe_key="cli:dev:mock",
                        source_task_id=None,
                        review_type="manual_review",
                        review_status=ReviewTaskStatus.PENDING,
                        created_at=datetime(2026, 7, 17, 10, 0),
                        updated_at=datetime(2026, 7, 17, 10, 0),
                    )
                )[2]
                output = io.StringIO()
                args = ["cli", "notification-worker", "--runtime-db", str(db_path)]
                with patch.object(sys, "argv", args), redirect_stdout(output):
                    exit_code = main()
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                repository.get_notification_outbox(created.notification_id).status,
                NotificationOutboxStatus.SENT.value,
            )

    def test_resolve_manual_task_returns_non_zero_and_deprecated_message(self) -> None:
        args = [
            "cli",
            "resolve-manual-task",
            "--tasks",
            "dummy.xlsx",
            "--output",
            "dummy-out.xlsx",
            "--task-id",
            "TASK-1",
            "--decision",
            "approve",
        ]
        output = io.StringIO()
        with patch.object(sys, "argv", args), redirect_stdout(output):
            exit_code = main()
        self.assertEqual(exit_code, 1)
        self.assertIn("已弃用", output.getvalue())

    def test_expire_review_tasks_cli_supports_dry_run_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime.sqlite3"
            repository = SQLiteRuntimeRepository(db_path)
            task_service = RuntimeTaskService(repository)
            task_service.init_schema()
            source = _runtime_task("TASK-1", required_by=datetime(2026, 5, 4, 8, 0))
            task_service.create_tasks([source])
            ReviewTaskService(repository, runtime_task_service=task_service).create_from_tasks([source])

            dry_run_output = io.StringIO()
            dry_run_args = [
                "cli",
                "expire-review-tasks",
                "--runtime-db",
                str(db_path),
                "--now",
                "2026-05-04T09:30:00",
            ]
            with patch.object(sys, "argv", dry_run_args), redirect_stdout(dry_run_output):
                exit_code = main()
            self.assertEqual(exit_code, 0)
            self.assertIn("mode=dry-run", dry_run_output.getvalue())
            self.assertEqual(task_service.list_tasks()[0].task_status, TaskStatus.MANUAL_REVIEW)

            apply_output = io.StringIO()
            apply_args = dry_run_args + ["--apply"]
            with patch.object(sys, "argv", apply_args), redirect_stdout(apply_output):
                exit_code = main()
            self.assertEqual(exit_code, 0)
            self.assertIn("mode=apply", apply_output.getvalue())
            self.assertEqual(task_service.get_task("TASK-1").task_status, TaskStatus.EXPIRED)


if __name__ == "__main__":
    unittest.main()

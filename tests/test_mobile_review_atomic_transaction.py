from __future__ import annotations

import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

from app.enums import ReviewTaskStatus, TaskActionType, TaskStatus
from app.exceptions import MobileReviewErrorCode, MobileReviewTransactionError
from app.models import Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.runtime import ReviewTaskService, ReviewTokenService, RuntimeTaskService
from app.services.workflow import resolve_mobile_review


def _task(task_id: str, status: TaskStatus = TaskStatus.MANUAL_REVIEW) -> Task:
    return Task(
        task_id=task_id,
        internal_sku="SKU-1",
        platform_name="platform",
        action_type=TaskActionType.MANUAL_REVIEW,
        priority=1,
        task_status=status,
        created_at=datetime(2026, 7, 15, 9, 0),
        result_message="needs review",
        trade_date=date(2026, 7, 15),
        scope_type="task",
        scope_key=task_id,
        dedupe_key=f"mobile-review|{task_id}",
    )


class MobileReviewAtomicTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "runtime.sqlite3"
        self.previous_secret = os.environ.get("REVIEW_TOKEN_SECRET")
        os.environ["REVIEW_TOKEN_SECRET"] = "atomic-mobile-review-test-secret"
        _, self.review_task_id, self.raw_token = self._prepare()

    def tearDown(self) -> None:
        if self.previous_secret is None:
            os.environ.pop("REVIEW_TOKEN_SECRET", None)
        else:
            os.environ["REVIEW_TOKEN_SECRET"] = self.previous_secret
        self.temp_dir.cleanup()

    def _prepare(self, db_path: Path | None = None) -> tuple[SQLiteRuntimeRepository, str, str]:
        repository = SQLiteRuntimeRepository(db_path or self.db_path)
        runtime_service = RuntimeTaskService(repository)
        runtime_service.init_schema()
        task = _task("TASK-ATOMIC")
        runtime_service.create_tasks([task])
        review_service = ReviewTaskService(repository, runtime_task_service=runtime_service)
        review_service.create_from_tasks([task])
        review = review_service.list_review_tasks(status=ReviewTaskStatus.PENDING)[0]
        token_service = ReviewTokenService(repository)
        token_result = token_service.create_token(
            review.review_task_id,
            token_subject="mobile_reviewer",
            expires_at=datetime.now() + timedelta(hours=1),
        )
        return repository, review.review_task_id, token_result.raw_token

    def test_success_commits_token_review_task_and_history_together(self) -> None:
        result = resolve_mobile_review(
            self.db_path,
            self.review_task_id,
            self.raw_token,
            "approved",
            note="approved by mobile",
            resolution_payload={"reviewer_code": "R-1", "adjustment": {"qty": 2}},
        )

        repository = SQLiteRuntimeRepository(self.db_path)
        review = repository.get_review_task(self.review_task_id)
        task = repository.get_task("TASK-ATOMIC")
        token = repository.get_review_token(result.review_token.token_id)
        history = repository.list_task_status_history("TASK-ATOMIC")
        self.assertEqual(review.review_status, ReviewTaskStatus.APPROVED)
        self.assertEqual(review.resolved_by, "mobile_reviewer")
        self.assertEqual(review.resolution_payload["reviewer_code"], "R-1")
        self.assertEqual(task.task_status, TaskStatus.PENDING)
        self.assertIsNotNone(token.used_at)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].to_status, TaskStatus.PENDING)

    def test_mobile_review_concurrent_same_token_only_one_can_commit(self) -> None:
        review_task_id, raw_token = self.review_task_id, self.raw_token
        start = threading.Barrier(2)

        def submit(_worker: str):
            start.wait(timeout=5)
            try:
                return ("ok", resolve_mobile_review(self.db_path, review_task_id, raw_token, "approved"))
            except MobileReviewTransactionError as exc:
                return ("error", exc.code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, ["first", "second"]))

        self.assertEqual(sum(item[0] == "ok" for item in results), 1)
        self.assertEqual(sum(item[0] == "error" for item in results), 1)
        self.assertIn(
            next(item[1] for item in results if item[0] == "error"),
            {MobileReviewErrorCode.TOKEN_ALREADY_USED, MobileReviewErrorCode.CONCURRENT_UPDATE},
        )
        repository = SQLiteRuntimeRepository(self.db_path)
        self.assertEqual(len(repository.list_task_status_history("TASK-ATOMIC")), 1)
        self.assertEqual(repository.get_review_task(review_task_id).resolved_by, "mobile_reviewer")

    def test_mobile_review_concurrent_different_tokens_cannot_commit_conflicting_resolution(self) -> None:
        repository = SQLiteRuntimeRepository(self.db_path)
        second = ReviewTokenService(repository).create_token(
            self.review_task_id,
            token_subject="second_mobile_reviewer",
        )
        start = threading.Barrier(2)

        def submit(raw_token: str, action: str):
            start.wait(timeout=5)
            try:
                return ("ok", resolve_mobile_review(self.db_path, self.review_task_id, raw_token, action))
            except MobileReviewTransactionError as exc:
                return ("error", exc.code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    submit,
                    [self.raw_token, second.raw_token],
                    ["approved", "rejected"],
                )
            )

        self.assertEqual(sum(item[0] == "ok" for item in results), 1)
        self.assertEqual(
            next(item[1] for item in results if item[0] == "error"),
            MobileReviewErrorCode.REVIEW_ALREADY_RESOLVED,
        )
        repository = SQLiteRuntimeRepository(self.db_path)
        self.assertEqual(len(repository.list_task_status_history("TASK-ATOMIC")), 1)

    def test_each_mobile_action_uses_the_expected_source_task_transition(self) -> None:
        expected = {
            "approved": TaskStatus.PENDING,
            "rejected": TaskStatus.SKIPPED,
            "adjusted": TaskStatus.SKIPPED,
            "cancelled": TaskStatus.CANCELLED,
        }
        for action, expected_task_status in expected.items():
            with self.subTest(action=action):
                point_db_path = Path(self.temp_dir.name) / f"action-{action}.sqlite3"
                _, review_task_id, raw_token = self._prepare(point_db_path)
                resolve_mobile_review(point_db_path, review_task_id, raw_token, action)
                self.assertEqual(
                    SQLiteRuntimeRepository(point_db_path).get_task("TASK-ATOMIC").task_status,
                    expected_task_status,
                )

    def test_faults_at_each_commit_stage_roll_back_everything(self) -> None:
        for point in (
            "after_token_update",
            "after_review_update",
            "after_task_update",
            "before_history_insert",
            "after_history_insert",
        ):
            with self.subTest(point=point):
                point_db_path = Path(self.temp_dir.name) / f"{point}.sqlite3"
                repository, review_task_id, raw_token = self._prepare(point_db_path)
                token_hash = ReviewTokenService(repository)._hash_raw_token(raw_token)

                def inject(current: str) -> None:
                    if current == point:
                        raise RuntimeError(f"injected failure at {current}")

                with self.assertRaises(RuntimeError):
                    repository.resolve_mobile_review_atomic(
                        review_task_id=review_task_id,
                        token_hash=token_hash,
                        status=ReviewTaskStatus.REJECTED,
                        actor_source="mobile_review_token",
                        note="injected",
                        resolution_payload={"fault": point},
                        failure_injector=inject,
                    )

                check = SQLiteRuntimeRepository(point_db_path)
                self.assertIsNone(check.get_review_token_by_hash(token_hash).used_at)
                self.assertEqual(
                    check.get_review_task(review_task_id).review_status,
                    ReviewTaskStatus.PENDING,
                )
                self.assertEqual(check.get_task("TASK-ATOMIC").task_status, TaskStatus.MANUAL_REVIEW)
                self.assertEqual(check.list_task_status_history("TASK-ATOMIC"), [])

    def test_reuse_returns_stable_token_error_without_new_history(self) -> None:
        resolve_mobile_review(self.db_path, self.review_task_id, self.raw_token, "approved")
        with self.assertRaises(MobileReviewTransactionError) as context:
            resolve_mobile_review(self.db_path, self.review_task_id, self.raw_token, "approved")

        self.assertEqual(context.exception.code, MobileReviewErrorCode.TOKEN_ALREADY_USED)
        self.assertEqual(
            len(SQLiteRuntimeRepository(self.db_path).list_task_status_history("TASK-ATOMIC")),
            1,
        )


if __name__ == "__main__":
    unittest.main()

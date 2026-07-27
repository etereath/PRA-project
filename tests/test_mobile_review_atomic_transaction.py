from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app.enums import ReviewTaskStatus, TaskActionType, TaskStatus
from app.exceptions import MobileReviewErrorCode, MobileReviewTransactionError
from app.models import Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository, _is_sqlite_concurrency_error
from app.services.runtime import ReviewTaskService, ReviewTokenService, RuntimeTaskService
from app.services.workflow import resolve_mobile_review


def _task(
    task_id: str,
    status: TaskStatus = TaskStatus.MANUAL_REVIEW,
    *,
    action_type: TaskActionType = TaskActionType.MANUAL_REVIEW,
    required_by: datetime | None = None,
) -> Task:
    return Task(
        task_id=task_id,
        internal_sku="SKU-1",
        platform_name="platform",
        action_type=action_type,
        priority=1,
        task_status=status,
        created_at=datetime(2026, 7, 15, 9, 0),
        result_message="needs review",
        trade_date=date(2026, 7, 15),
        scope_type="task",
        scope_key=task_id,
        dedupe_key=f"mobile-review|{task_id}",
        required_by=required_by,
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

    def test_token_expiry_is_checked_after_waiting_for_the_write_lock(self) -> None:
        repository, review_task_id, raw_token = self._prepare()
        expires_at = datetime.now() + timedelta(seconds=0.35)
        with closing(repository.connect()) as connection, connection:
            connection.execute(
                "UPDATE review_tokens SET expires_at = ? WHERE review_task_id = ?",
                (expires_at.isoformat(), review_task_id),
            )

        lock_connection = repository.connect()
        lock_connection.execute("BEGIN IMMEDIATE")
        started = threading.Event()
        outcome: dict[str, object] = {}

        def submit() -> None:
            started.set()
            try:
                outcome["result"] = resolve_mobile_review(
                    self.db_path,
                    review_task_id,
                    raw_token,
                    "approved",
                )
            except BaseException as exc:  # noqa: BLE001 - preserve the worker failure for the assertion below
                outcome["error"] = exc

        worker = threading.Thread(target=submit)
        worker.start()
        try:
            self.assertTrue(started.wait(timeout=2))
            time.sleep(0.75)
        finally:
            lock_connection.rollback()
            lock_connection.close()
        worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertNotIn("result", outcome)
        self.assertIsInstance(outcome.get("error"), MobileReviewTransactionError)
        self.assertEqual(
            outcome["error"].code,  # type: ignore[union-attr]
            MobileReviewErrorCode.TOKEN_EXPIRED,
        )
        check = SQLiteRuntimeRepository(self.db_path)
        token_hash = ReviewTokenService(repository)._hash_raw_token(raw_token)
        self.assertIsNone(check.get_review_token_by_hash(token_hash).used_at)
        self.assertEqual(check.get_review_task(review_task_id).review_status, ReviewTaskStatus.PENDING)
        self.assertEqual(check.get_task("TASK-ATOMIC").task_status, TaskStatus.MANUAL_REVIEW)
        self.assertEqual(check.list_task_status_history("TASK-ATOMIC"), [])

    def test_timezone_aware_future_token_can_be_resolved(self) -> None:
        repository = SQLiteRuntimeRepository(self.db_path)
        aware_expiry = datetime.now(UTC) + timedelta(hours=1)
        with closing(repository.connect()) as connection, connection:
            connection.execute(
                "UPDATE review_tokens SET expires_at = ? WHERE review_task_id = ?",
                (aware_expiry.isoformat(), self.review_task_id),
            )

        result = resolve_mobile_review(
            self.db_path,
            self.review_task_id,
            self.raw_token,
            "approved",
        )

        self.assertEqual(result.review_task.review_status, ReviewTaskStatus.APPROVED)
        self.assertEqual(result.source_task_status, TaskStatus.PENDING)

    def test_execution_retry_refreshes_stale_task_deadline(self) -> None:
        db_path = Path(self.temp_dir.name) / "execution-retry-deadline.sqlite3"
        repository = SQLiteRuntimeRepository(db_path)
        runtime_service = RuntimeTaskService(repository)
        runtime_service.init_schema()
        source = _task(
            "TASK-EXECUTION-RETRY",
            action_type=TaskActionType.UPDATE_PRICE,
            required_by=datetime(2026, 7, 26, 6, 18),
        )
        runtime_service.create_tasks([source])
        review_service = ReviewTaskService(
            repository,
            runtime_task_service=runtime_service,
        )
        review = review_service.create_from_tasks([source]).review_tasks[0]
        resolved_at = datetime(2026, 7, 26, 9, 0)
        token = ReviewTokenService(repository).create_token(
            review.review_task_id,
            token_subject="mobile_reviewer",
            expires_at=resolved_at + timedelta(hours=1),
        )

        result = repository.resolve_mobile_review_atomic(
            review_task_id=review.review_task_id,
            token_hash=token.review_token.token_hash,
            status=ReviewTaskStatus.APPROVED,
            actor_source="mobile_review_token",
            now=resolved_at,
        )

        retried = repository.get_task(source.task_id)
        expected_deadline = resolved_at + timedelta(minutes=30)
        self.assertEqual(result.source_task_status, TaskStatus.PENDING)
        self.assertEqual(retried.required_by, expected_deadline)
        self.assertEqual(retried.expires_at, expected_deadline)
        self.assertEqual(
            result.review_task.resolution_payload["retry_required_by"],
            expected_deadline.isoformat(),
        )
        self.assertEqual(
            repository.list_task_status_history(source.task_id)[-1].metadata[
                "retry_required_by"
            ],
            expected_deadline.isoformat(),
        )
        self.assertEqual(
            runtime_service.expire_overdue_pending_tasks(
                now=resolved_at + timedelta(minutes=1)
            ),
            0,
        )

    def test_each_mobile_action_uses_the_expected_source_task_transition(self) -> None:
        expected = {
            "approved": TaskStatus.PENDING,
            "rejected": TaskStatus.SKIPPED,
            "adjusted": TaskStatus.PENDING,
            "cancelled": TaskStatus.CANCELLED,
        }
        for action, expected_task_status in expected.items():
            with self.subTest(action=action):
                point_db_path = Path(self.temp_dir.name) / f"action-{action}.sqlite3"
                _, review_task_id, raw_token = self._prepare(point_db_path)
                payload = (
                    {"adjustment": {"target_price": "8.80", "target_status": "online", "result_message": "adjusted"}}
                    if action == "adjusted"
                    else None
                )
                resolve_mobile_review(
                    point_db_path,
                    review_task_id,
                    raw_token,
                    action,
                    resolution_payload=payload,
                )
                repository = SQLiteRuntimeRepository(point_db_path)
                task = repository.get_task("TASK-ATOMIC")
                self.assertEqual(task.task_status, expected_task_status)
                if action == "adjusted":
                    self.assertEqual(task.target_price, Decimal("8.8"))
                    self.assertEqual(task.target_status, "online")
                    self.assertEqual(task.result_message, "adjusted")
                    self.assertEqual(
                        task.decision_trace["mobile_review_adjustment"]["target_price"],
                        "8.8",
                    )

    def test_faults_at_each_commit_stage_roll_back_everything(self) -> None:
        for point in (
            "after_token_update",
            "after_review_update",
            "after_task_update",
            "before_history_insert",
            "after_history_insert",
            "before_result_conversion",
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

    def test_adjusted_requires_a_valid_normalized_payload(self) -> None:
        repository, review_task_id, raw_token = self._prepare(
            Path(self.temp_dir.name) / "invalid-adjustment.sqlite3"
        )
        token_hash = ReviewTokenService(repository)._hash_raw_token(raw_token)

        with self.assertRaises(MobileReviewTransactionError) as context:
            repository.resolve_mobile_review_atomic(
                review_task_id=review_task_id,
                token_hash=token_hash,
                status=ReviewTaskStatus.ADJUSTED,
                actor_source="mobile_review_token",
                resolution_payload={},
            )

        self.assertEqual(context.exception.code, MobileReviewErrorCode.INVALID_ADJUSTMENT)
        check = SQLiteRuntimeRepository(Path(self.temp_dir.name) / "invalid-adjustment.sqlite3")
        self.assertEqual(check.get_review_task(review_task_id).review_status, ReviewTaskStatus.PENDING)
        self.assertEqual(check.get_task("TASK-ATOMIC").task_status, TaskStatus.MANUAL_REVIEW)

    def test_missing_or_incompatible_source_task_fails_before_token_consumption(self) -> None:
        scenarios = (
            ("missing-row", "approved", None, MobileReviewErrorCode.SOURCE_TASK_NOT_FOUND),
            (
                "null-source-adjusted",
                "adjusted",
                {"adjustment": {"target_status": "online"}},
                MobileReviewErrorCode.SOURCE_TASK_NOT_FOUND,
            ),
            ("incompatible-status", "approved", None, MobileReviewErrorCode.CONCURRENT_UPDATE),
        )
        for scenario, action, payload, expected_code in scenarios:
            with self.subTest(scenario=scenario):
                point_db_path = Path(self.temp_dir.name) / f"source-guard-{scenario}.sqlite3"
                repository, review_task_id, raw_token = self._prepare(point_db_path)
                if scenario == "missing-row":
                    with closing(repository.connect()) as connection:
                        connection.execute("PRAGMA foreign_keys = OFF")
                        connection.execute(
                            "UPDATE review_tasks SET source_task_id = ? WHERE review_task_id = ?",
                            ("MISSING-SOURCE", review_task_id),
                        )
                        connection.commit()
                elif scenario == "null-source-adjusted":
                    with closing(repository.connect()) as connection, connection:
                        connection.execute(
                            "UPDATE review_tasks SET source_task_id = NULL WHERE review_task_id = ?",
                            (review_task_id,),
                        )
                else:
                    repository.update_task_status("TASK-ATOMIC", TaskStatus.RUNNING)

                token_hash = ReviewTokenService(repository)._hash_raw_token(raw_token)
                with self.assertRaises(MobileReviewTransactionError) as context:
                    resolve_mobile_review(
                        point_db_path,
                        review_task_id,
                        raw_token,
                        action,
                        resolution_payload=payload,
                    )

                self.assertEqual(context.exception.code, expected_code)
                check = SQLiteRuntimeRepository(point_db_path)
                self.assertIsNone(check.get_review_token_by_hash(token_hash).used_at)
                self.assertEqual(check.get_review_task(review_task_id).review_status, ReviewTaskStatus.PENDING)
                if scenario == "incompatible-status":
                    self.assertEqual(check.get_task("TASK-ATOMIC").task_status, TaskStatus.RUNNING)
                else:
                    self.assertEqual(check.get_task("TASK-ATOMIC").task_status, TaskStatus.MANUAL_REVIEW)
                self.assertEqual(check.list_task_status_history("TASK-ATOMIC"), [])

    def test_sqlite_concurrency_classification_uses_codes_not_localized_text(self) -> None:
        class CodedOperationalError(sqlite3.OperationalError):
            def __init__(self, message: str, code: int, name: str):
                super().__init__(message)
                self.sqlite_errorcode = code
                self.sqlite_errorname = name

        self.assertTrue(
            _is_sqlite_concurrency_error(
                CodedOperationalError("数据库正忙", sqlite3.SQLITE_BUSY, "SQLITE_BUSY")
            )
        )
        self.assertTrue(
            _is_sqlite_concurrency_error(
                CodedOperationalError("snapshot verrouillé", sqlite3.SQLITE_BUSY_SNAPSHOT, "SQLITE_BUSY_SNAPSHOT")
            )
        )
        self.assertTrue(
            _is_sqlite_concurrency_error(
                CodedOperationalError(
                    "共享缓存被占用",
                    sqlite3.SQLITE_LOCKED_SHAREDCACHE,
                    "SQLITE_LOCKED_SHAREDCACHE",
                )
            )
        )
        self.assertFalse(
            _is_sqlite_concurrency_error(
                CodedOperationalError("database is locked", sqlite3.SQLITE_ERROR, "SQLITE_ERROR")
            )
        )
        self.assertFalse(_is_sqlite_concurrency_error(sqlite3.OperationalError("database is locked")))

    def test_result_conversion_failure_rolls_back_before_commit(self) -> None:
        for converter_name in ("_row_to_review_task", "_row_to_review_token", "_row_to_task"):
            with self.subTest(converter=converter_name):
                point_db_path = Path(self.temp_dir.name) / f"conversion-{converter_name}.sqlite3"
                repository, review_task_id, raw_token = self._prepare(point_db_path)
                token_hash = ReviewTokenService(repository)._hash_raw_token(raw_token)

                with patch(
                    f"app.repositories.sqlite_runtime_repository.{converter_name}",
                    side_effect=ValueError(f"damaged row in {converter_name}"),
                ):
                    with self.assertRaises(ValueError):
                        repository.resolve_mobile_review_atomic(
                            review_task_id=review_task_id,
                            token_hash=token_hash,
                            status=ReviewTaskStatus.APPROVED,
                            actor_source="mobile_review_token",
                        )

                check = SQLiteRuntimeRepository(point_db_path)
                self.assertIsNone(check.get_review_token_by_hash(token_hash).used_at)
                self.assertEqual(check.get_review_task(review_task_id).review_status, ReviewTaskStatus.PENDING)
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

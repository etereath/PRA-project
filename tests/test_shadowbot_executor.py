from __future__ import annotations

import os
import hashlib
import shutil
import unittest
from argparse import Namespace
from datetime import date, datetime, timedelta, UTC
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.enums import NotificationOutboxStatus, ReviewTaskStatus, TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.models import ReviewTask, Task
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.runtime import RuntimeTaskService
from app.services.shadowbot_executor import (
    EXECUTION_MODE_COMMIT,
    EXECUTION_MODE_RECONCILE,
    FileDropShadowBotTaskRunner,
    SHADOWBOT_EXECUTOR_NAME,
    SIDE_EFFECT_NOT_STARTED,
    SIDE_EFFECT_SUBMIT_INTENT_RECORDED,
    SIDE_EFFECT_UNKNOWN,
    STATUS_FAILED,
    STATUS_NOT_APPLIED,
    STATUS_NEEDS_RECONCILIATION,
    STATUS_READ_COMPLETED,
    STATUS_RUNNING,
    STATUS_VERIFIED,
    ShadowBotApproval,
    ShadowBotApprovedPayload,
    ShadowBotExecutionRequest,
    ShadowBotExecutor,
    ShadowBotResultContract,
    ShadowBotStartResult,
    YingdaoOpenApiJobRunner,
    build_shadowbot_task_runner_from_environment,
    compute_approved_payload_hash,
    compute_instruction_hash,
)
from scripts.run_shadowbot_executor import (
    check_yingdao_app_params,
    record_result_from_file,
    record_result_from_yingdao_job_query,
    start_from_args,
)
from scripts.check_shadowbot_readiness import build_readiness_report
from scripts.prepare_shadowbot_e2e_chain import prepare_shadowbot_chain_from_args
from scripts.run_shadowbot_e2e_local_demo import run_local_demo_from_args


class FakeShadowBotRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def start(self, payload: dict[str, object]) -> ShadowBotStartResult:
        self.calls.append(payload)
        return ShadowBotStartResult(
            shadowbot_run_id=f"RUN-{len(self.calls)}",
            raw_output={"accepted": True},
        )


def _task(task_id: str = "TASK-SB-1") -> Task:
    return Task(
        task_id=task_id,
        internal_sku="SKU-AISHA-C",
        platform_name="蚂蚁花团供应商",
        action_type=TaskActionType.UPDATE_PRICE,
        priority=1,
        task_status=TaskStatus.PENDING,
        created_at=datetime(2026, 6, 23, 9, 0),
        target_price=Decimal("19.50"),
        trade_date=date(2026, 6, 23),
        scope_type="sku",
        scope_key="SKU-AISHA-C",
        dedupe_key=f"{task_id}|update_price",
    )


def _approved_payload(operation_id: str = "OP-1", task_id: str = "TASK-SB-1") -> ShadowBotApprovedPayload:
    return ShadowBotApprovedPayload(
        operation_id=operation_id,
        task_id=task_id,
        platform="蚂蚁花团供应商",
        product_identity={
            "internal_sku": "SKU-AISHA-C",
            "platform_sku": "SKU-AISHA-C",
            "name": "艾莎",
            "grade": "C级",
        },
        expected_old_price=Decimal("19.00"),
        target_price=Decimal("19.50"),
    )


def _approval(payload: ShadowBotApprovedPayload | None = None) -> ShadowBotApproval:
    payload = payload or _approved_payload()
    return ShadowBotApproval(
        approval_id="APPROVAL-1",
        approval_status="APPROVED",
        approved_payload=payload,
        approved_payload_hash=compute_approved_payload_hash(payload),
        approved_at=datetime(2026, 6, 23, 9, 5, tzinfo=UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _review_task(payload: ShadowBotApprovedPayload | None = None) -> ReviewTask:
    payload = payload or _approved_payload()
    return ReviewTask(
        review_task_id="APPROVAL-1",
        trade_date=date(2026, 6, 23),
        scope_type="sku",
        scope_key="SKU-AISHA-C",
        dedupe_key="APPROVAL-1",
        source_task_id=payload.task_id,
        review_type="price_update",
        review_status=ReviewTaskStatus.APPROVED,
        internal_sku="SKU-AISHA-C",
        platform_name=payload.platform,
        reason="approved shadowbot price update",
        review_payload={"approved_payload_hash": compute_approved_payload_hash(payload)},
        resolution_payload={"approved_payload_hash": compute_approved_payload_hash(payload)},
        created_at=datetime(2026, 6, 23, 9, 0),
        updated_at=datetime(2026, 6, 23, 9, 5),
        resolved_by="alice",
        resolved_at=datetime(2026, 6, 23, 9, 5),
    )


def _review_task_for_payload(review_task_id: str, payload: ShadowBotApprovedPayload) -> ReviewTask:
    review = _review_task(payload)
    review.review_task_id = review_task_id
    review.dedupe_key = review_task_id
    return review


def _result_binding(repository: SQLiteRuntimeRepository, execution_attempt_id: str) -> dict[str, object]:
    attempt = repository.get_shadowbot_execution_attempt(execution_attempt_id)
    operation = repository.get_shadowbot_operation(attempt.operation_id)
    return {
        "operation_id": operation.operation_id,
        "task_id": operation.task_id,
        "execution_mode": attempt.execution_mode,
        "instruction_hash": attempt.instruction_hash,
        "request_file_sha256": attempt.request_file_sha256,
        "worker_id": "test-worker",
        **_lease_binding(repository, execution_attempt_id),
    }


def _lease_binding(repository: SQLiteRuntimeRepository, execution_attempt_id: str) -> dict[str, object]:
    attempt = repository.get_shadowbot_execution_attempt(execution_attempt_id)
    lease = attempt.raw_output["lease"]
    return {
        "lease_owner_token": str(lease["owner_token"]),
        "lease_version": int(lease["version"]),
    }


def _queue_payload(execution_attempt_id: str) -> dict[str, object]:
    payload = {
        "schema_version": "shadowbot-request-1.0",
        "task_id": "TASK-SB-1",
        "operation_id": "OP-1",
        "execution_attempt_id": execution_attempt_id,
        "execution_mode": EXECUTION_MODE_RECONCILE,
        "platform_name": "蚂蚁花团供应商",
        "platform_sku": "SKU-AISHA-C",
        "product_keyword": "C级艾莎",
        "expected_product_name": "艾莎",
        "expected_grade": "C级",
        "expected_spec": "",
        "spec_verification_required": False,
        "expected_old_price": "19.00",
        "target_price": "19.50",
        "approved_payload_hash": "approved-hash",
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
    }
    payload["instruction_hash"] = compute_instruction_hash(payload)
    return payload


class ShadowBotExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = Path.cwd() / "test_runtime_tmp" / "shadowbot_executor_tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp_path = temp_root / f"case_{uuid4().hex}"
        self.temp_path.mkdir(parents=True, exist_ok=False)
        self.db_path = self.temp_path / "runtime.sqlite3"
        self.repository = SQLiteRuntimeRepository(self.db_path)
        self.runtime_service = RuntimeTaskService(self.repository)
        self.runtime_service.init_schema()
        self.runtime_service.create_tasks([_task()])
        self.repository.insert_review_tasks([_review_task()])
        self.runner = FakeShadowBotRunner()
        self.executor = ShadowBotExecutor(self.repository, self.runner)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_path, ignore_errors=True)

    def test_start_execution_validates_approval_hash_and_does_not_start_runner_on_mismatch(self) -> None:
        approval = _approval()
        approval.approved_payload_hash = "bad-hash"

        with self.assertRaises(ValidationError):
            self.executor.start_execution(
                ShadowBotExecutionRequest(
                    operation_id="OP-1",
                    execution_attempt_id="ATTEMPT-1",
                    execution_mode=EXECUTION_MODE_COMMIT,
                    approval=approval,
                )
            )

        self.assertEqual(self.runner.calls, [])
        self.assertIsNone(self.repository.get_shadowbot_operation("OP-1"))

    def test_login_verification_handoff_is_idempotent_and_notifies_without_credentials(self) -> None:
        self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-LOGIN-1",
                execution_mode=EXECUTION_MODE_COMMIT,
                approval=_approval(),
            )
        )
        phase = {
            "execution_attempt_id": "ATTEMPT-LOGIN-1",
            "phase": "LOGIN_VERIFICATION_REQUIRED",
            "login": {
                "verification_detected_at": "2026-07-11T10:00:00+08:00",
                "verification_deadline_at": "2026-07-11T10:05:00+08:00",
                "verification_markers": ["验证码"],
            },
        }
        with patch.dict(os.environ, {"DEFAULT_NOTIFICATION_CHANNEL": "mock"}, clear=False):
            first = self.executor.open_login_verification_handoff(phase)
            second = self.executor.open_login_verification_handoff(phase)

        review = self.repository.get_review_task(first)
        notifications = self.repository.list_notification_logs(related_review_task_id=first)
        self.assertEqual(first, second)
        self.assertEqual(review.review_status, ReviewTaskStatus.PENDING)
        self.assertEqual(len(notifications), 1)
        self.assertNotIn("password", str(review.review_payload).lower())
        self.assertIn("ShadowBot 登录验证码人工接管", notifications[0].message)
        self.assertNotIn("Please complete", notifications[0].message)

    def test_login_verification_handoff_queues_outbox_for_real_channel_without_sending(self) -> None:
        self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-LOGIN-REAL-1",
                execution_mode=EXECUTION_MODE_COMMIT,
                approval=_approval(),
            )
        )
        phase = {
            "execution_attempt_id": "ATTEMPT-LOGIN-REAL-1",
            "phase": "LOGIN_VERIFICATION_REQUIRED",
            "login": {
                "verification_detected_at": "2026-07-17T10:00:00+08:00",
                "verification_deadline_at": "2026-07-17T10:05:00+08:00",
                "verification_markers": ["验证码"],
            },
        }
        with patch.dict(os.environ, {"DEFAULT_NOTIFICATION_CHANNEL": "feishu"}, clear=False):
            review_id = self.executor.open_login_verification_handoff(phase)

        outbox = self.repository.list_notification_outbox(related_review_task_id=review_id)
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0].notification_type, "verification_code_intervention")
        self.assertEqual(outbox[0].channel, "feishu")
        self.assertEqual(outbox[0].status, NotificationOutboxStatus.PENDING.value)
        self.assertEqual(self.repository.list_notification_delivery_attempts(outbox[0].notification_id), [])
        self.assertEqual(self.repository.get_notification_log(outbox[0].notification_id).send_status, "pending")

    def test_start_execution_requires_persisted_approved_review_task(self) -> None:
        approval = _approval()
        approval.approval_id = "MISSING-APPROVAL"

        with self.assertRaises(ValidationError):
            self.executor.start_execution(
                ShadowBotExecutionRequest(
                    operation_id="OP-1",
                    execution_attempt_id="ATTEMPT-1",
                    execution_mode=EXECUTION_MODE_COMMIT,
                    approval=approval,
                )
            )

        self.assertEqual(self.runner.calls, [])
        self.assertIsNone(self.repository.get_shadowbot_operation("OP-1"))

    def test_start_execution_creates_operation_attempt_lock_and_starts_shadowbot(self) -> None:
        result = self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-1",
                execution_mode=EXECUTION_MODE_COMMIT,
                approval=_approval(),
            )
        )

        self.assertEqual(result.status, STATUS_RUNNING)
        self.assertEqual(result.shadowbot_run_id, "RUN-1")
        self.assertEqual(len(self.runner.calls), 1)
        operation = self.repository.get_shadowbot_operation("OP-1")
        attempt = self.repository.get_shadowbot_execution_attempt("ATTEMPT-1")
        self.assertIsNotNone(operation)
        self.assertIsNotNone(attempt)
        self.assertEqual(operation.lock_owner, SHADOWBOT_EXECUTOR_NAME)
        self.assertEqual(attempt.shadowbot_run_id, "RUN-1")
        self.assertEqual(attempt.side_effect_state, SIDE_EFFECT_NOT_STARTED)

    def test_duplicate_execution_attempt_id_is_rejected(self) -> None:
        request = ShadowBotExecutionRequest(
            operation_id="OP-1",
            execution_attempt_id="ATTEMPT-1",
            execution_mode=EXECUTION_MODE_COMMIT,
            approval=_approval(),
        )
        self.executor.start_execution(request)
        self.executor.record_result(
            ShadowBotResultContract(
                execution_attempt_id="ATTEMPT-1",
                status=STATUS_FAILED,
                run_success_flag=False,
                business_operation_completed=False,
                side_effect_state=SIDE_EFFECT_NOT_STARTED,
                retryable=True,
                error_code="PRE_SUBMIT_FAILED",
                **_lease_binding(self.repository, "ATTEMPT-1"),
            )
        )

        with self.assertRaises(ValidationError):
            self.executor.start_execution(request)

    def test_lock_blocks_another_owner(self) -> None:
        self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-1",
                execution_mode=EXECUTION_MODE_COMMIT,
                approval=_approval(),
                lock_owner="worker-a",
            )
        )

        with self.assertRaises(ValidationError):
            self.executor.start_execution(
                ShadowBotExecutionRequest(
                    operation_id="OP-1",
                    execution_attempt_id="ATTEMPT-2",
                    execution_mode=EXECUTION_MODE_COMMIT,
                    approval=_approval(),
                    lock_owner="worker-b",
                )
            )

    def test_checkpoint_after_submit_intent_forces_timeout_to_reconcile(self) -> None:
        self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-1",
                execution_mode=EXECUTION_MODE_COMMIT,
                approval=_approval(),
            )
        )
        checkpoint = self.executor.record_side_effect_checkpoint(
            operation_id="OP-1",
            execution_attempt_id="ATTEMPT-1",
            side_effect_state=SIDE_EFFECT_SUBMIT_INTENT_RECORDED,
            **_lease_binding(self.repository, "ATTEMPT-1"),
        )

        with self.assertRaisesRegex(ValidationError, "SHADOWBOT_LEASE_STILL_ACTIVE"):
            self.executor.classify_timeout("OP-1")
        self.assertEqual(self.repository.get_shadowbot_operation("OP-1").status, STATUS_RUNNING)
        self.assertEqual(self.repository.get_shadowbot_execution_attempt("ATTEMPT-1").status, STATUS_RUNNING)

        attempt = self.repository.get_shadowbot_execution_attempt("ATTEMPT-1")
        expires_at = datetime.fromisoformat(str(attempt.raw_output["lease"]["expires_at"]))
        classification = self.executor.classify_timeout("OP-1", now=expires_at + timedelta(seconds=1))

        self.assertEqual(checkpoint.version, 1)
        self.assertEqual(classification.status, STATUS_NEEDS_RECONCILIATION)
        self.assertEqual(classification.next_execution_mode, EXECUTION_MODE_RECONCILE)
        self.assertFalse(classification.retryable)
        self.assertNotIn(
            self.repository.get_shadowbot_execution_attempt("ATTEMPT-1").status,
            {"STARTING", "RUNNING"},
        )

    def test_commit_start_after_submit_intent_checkpoint_does_not_start_new_price_attempt(self) -> None:
        self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-1",
                execution_mode=EXECUTION_MODE_COMMIT,
                approval=_approval(),
            )
        )
        self.executor.record_side_effect_checkpoint(
            operation_id="OP-1",
            execution_attempt_id="ATTEMPT-1",
            side_effect_state=SIDE_EFFECT_SUBMIT_INTENT_RECORDED,
            **_lease_binding(self.repository, "ATTEMPT-1"),
        )
        self.executor.record_result(
            ShadowBotResultContract(
                execution_attempt_id="ATTEMPT-1",
                status=STATUS_NEEDS_RECONCILIATION,
                run_success_flag=None,
                business_operation_completed=None,
                side_effect_state=SIDE_EFFECT_UNKNOWN,
                retryable=False,
                error_code="SUBMIT_RESULT_UNKNOWN",
                **_lease_binding(self.repository, "ATTEMPT-1"),
            )
        )

        result = self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-2",
                execution_mode=EXECUTION_MODE_COMMIT,
                approval=_approval(),
            )
        )

        self.assertEqual(result.status, STATUS_NEEDS_RECONCILIATION)
        self.assertEqual(result.next_execution_mode, EXECUTION_MODE_RECONCILE)
        self.assertEqual(len(self.runner.calls), 2)
        self.assertIsNone(self.repository.get_shadowbot_execution_attempt("ATTEMPT-2"))

    def test_result_contract_rejects_success_flag_that_would_complete_business_on_read(self) -> None:
        self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-1",
                execution_mode=EXECUTION_MODE_COMMIT,
                approval=_approval(),
            )
        )

        with self.assertRaises(ValidationError):
            self.executor.record_result(
                ShadowBotResultContract(
                    execution_attempt_id="ATTEMPT-1",
                    status=STATUS_READ_COMPLETED,
                    run_success_flag=True,
                    business_operation_completed=True,
                    side_effect_state=SIDE_EFFECT_NOT_STARTED,
                    retryable=False,
                )
            )

    def test_success_result_writes_execution_log_marks_operation_verified_and_completes_task(self) -> None:
        self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-1",
                execution_mode=EXECUTION_MODE_COMMIT,
                approval=_approval(),
            )
        )

        self.executor.record_result(
            ShadowBotResultContract(
                execution_attempt_id="ATTEMPT-1",
                status=STATUS_VERIFIED,
                run_success_flag=True,
                business_operation_completed=True,
                side_effect_state="VERIFIED",
                retryable=False,
                raw_output={
                    "old_price": "19.00",
                    "target_price": "19.50",
                    "actual_price": "19.50",
                    "evidence_status": "COMPLETE",
                    "evidence": [{"type": "AFTER_SUBMIT", "storage_uri": r"\\share\after.png"}],
                },
                **_lease_binding(self.repository, "ATTEMPT-1"),
            )
        )

        operation = self.repository.get_shadowbot_operation("OP-1")
        task = self.repository.get_task("TASK-SB-1")
        logs = self.repository.list_execution_logs(task_id="TASK-SB-1")
        self.assertEqual(operation.status, STATUS_VERIFIED)
        self.assertEqual(task.task_status, TaskStatus.SUCCESS)
        self.assertEqual(len(logs), 1)
        self.assertTrue(logs[0].success_flag)
        self.assertIn('"operation_id": "OP-1"', logs[0].raw_output)
        self.assertIn('"shadowbot_run_id": "RUN-1"', logs[0].raw_output)

    def test_pre_submit_failure_writes_log_and_marks_task_failed_without_reconcile_freeze(self) -> None:
        self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-1",
                execution_mode=EXECUTION_MODE_COMMIT,
                approval=_approval(),
            )
        )

        self.executor.record_result(
            ShadowBotResultContract(
                execution_attempt_id="ATTEMPT-1",
                status=STATUS_FAILED,
                run_success_flag=False,
                business_operation_completed=False,
                side_effect_state=SIDE_EFFECT_NOT_STARTED,
                retryable=True,
                error_code="PRODUCT_NOT_FOUND",
                raw_output={"status": STATUS_FAILED, "side_effect_state": SIDE_EFFECT_NOT_STARTED},
                **_lease_binding(self.repository, "ATTEMPT-1"),
            )
        )

        operation = self.repository.get_shadowbot_operation("OP-1")
        task = self.repository.get_task("TASK-SB-1")
        logs = self.repository.list_execution_logs(task_id="TASK-SB-1")
        self.assertEqual(operation.status, STATUS_FAILED)
        self.assertEqual(task.task_status, TaskStatus.FAILED)
        self.assertEqual(logs[0].error_code, "PRODUCT_NOT_FOUND")
        self.assertFalse(logs[0].success_flag)

    def test_unknown_after_submit_freezes_operation_and_allows_only_reconcile_attempt(self) -> None:
        self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-1",
                execution_mode=EXECUTION_MODE_COMMIT,
                approval=_approval(),
            )
        )
        self.executor.record_side_effect_checkpoint(
            operation_id="OP-1",
            execution_attempt_id="ATTEMPT-1",
            side_effect_state=SIDE_EFFECT_SUBMIT_INTENT_RECORDED,
            **_lease_binding(self.repository, "ATTEMPT-1"),
        )
        self.executor.record_result(
            ShadowBotResultContract(
                execution_attempt_id="ATTEMPT-1",
                status=STATUS_NEEDS_RECONCILIATION,
                run_success_flag=None,
                business_operation_completed=None,
                side_effect_state=SIDE_EFFECT_UNKNOWN,
                retryable=False,
                error_code="SUBMIT_RESULT_UNKNOWN",
                raw_output={"status": STATUS_NEEDS_RECONCILIATION, "side_effect_state": SIDE_EFFECT_UNKNOWN},
                **_lease_binding(self.repository, "ATTEMPT-1"),
            )
        )

        blocked_commit = self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-2",
                execution_mode=EXECUTION_MODE_COMMIT,
                approval=_approval(),
            )
        )
        reconcile_id = "RECONCILE-" + hashlib.sha256(b"ATTEMPT-1").hexdigest()[:20]
        reconcile = self.repository.get_shadowbot_execution_attempt(reconcile_id)

        self.assertEqual(blocked_commit.status, STATUS_NEEDS_RECONCILIATION)
        self.assertEqual(blocked_commit.next_execution_mode, EXECUTION_MODE_RECONCILE)
        self.assertEqual(reconcile.status, STATUS_RUNNING)
        self.assertEqual(len(self.runner.calls), 2)

    def test_reconcile_not_applied_is_not_mixed_with_failed_status(self) -> None:
        self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-1",
                execution_mode=EXECUTION_MODE_RECONCILE,
                approval=_approval(),
            )
        )

        self.executor.record_result(
            ShadowBotResultContract(
                execution_attempt_id="ATTEMPT-1",
                status=STATUS_NOT_APPLIED,
                run_success_flag=True,
                business_operation_completed=False,
                side_effect_state="NOT_APPLIED",
                retryable=False,
                error_code="SUBMIT_NOT_APPLIED",
                raw_output={"actual_price": "19.00"},
                **_lease_binding(self.repository, "ATTEMPT-1"),
            )
        )

        operation = self.repository.get_shadowbot_operation("OP-1")
        logs = self.repository.list_execution_logs(task_id="TASK-SB-1")
        self.assertEqual(operation.status, STATUS_NOT_APPLIED)
        self.assertIn('"status": "NOT_APPLIED"', logs[0].raw_output)

    def test_start_reconcile_attempt_from_frozen_operation_uses_read_only_mode(self) -> None:
        self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-1",
                execution_mode=EXECUTION_MODE_COMMIT,
                approval=_approval(),
            )
        )
        self.executor.record_side_effect_checkpoint(
            operation_id="OP-1",
            execution_attempt_id="ATTEMPT-1",
            side_effect_state=SIDE_EFFECT_SUBMIT_INTENT_RECORDED,
            **_lease_binding(self.repository, "ATTEMPT-1"),
        )
        self.executor.record_result(
            ShadowBotResultContract(
                execution_attempt_id="ATTEMPT-1",
                status=STATUS_NEEDS_RECONCILIATION,
                run_success_flag=None,
                business_operation_completed=None,
                side_effect_state=SIDE_EFFECT_UNKNOWN,
                retryable=False,
                error_code="SUBMIT_RESULT_UNKNOWN",
                **_lease_binding(self.repository, "ATTEMPT-1"),
            )
        )

        result = self.executor.ensure_reconcile_attempt(
            operation_id="OP-1",
            source_execution_attempt_id="ATTEMPT-1",
        )

        self.assertEqual(result.status, STATUS_RUNNING)
        self.assertEqual(self.runner.calls[-1]["execution_mode"], EXECUTION_MODE_RECONCILE)
        self.assertEqual(self.runner.calls[-1]["operation_id"], "OP-1")

    def test_file_drop_runner_writes_request_payload(self) -> None:
        request_dir = self.temp_path / "shadowbot_requests"
        runner = FileDropShadowBotTaskRunner(request_dir)

        result = runner.start(_queue_payload("ATTEMPT-FILE-1"))

        request_path = request_dir / "inbox" / "ATTEMPT-FILE-1.ready.json"
        self.assertTrue(request_path.exists())
        self.assertEqual(result.shadowbot_run_id, "filequeue:ATTEMPT-FILE-1")
        self.assertIn('"execution_mode": "RECONCILE"', request_path.read_text(encoding="utf-8"))

    def test_yingdao_openapi_runner_starts_job_with_payload_params(self) -> None:
        requests = []

        def fake_request(request, timeout):
            requests.append(request)
            url = request.get_full_url()
            if url.startswith("https://api.example.test/oapi/token/v2/token/create"):
                return {"success": True, "code": 200, "data": {"accessToken": "TOKEN-1", "expiresIn": 7200}}
            if url == "https://api.example.test/oapi/dispatch/v2/job/start":
                body = json.loads(request.data.decode("utf-8"))
                self.assertEqual(body["robotUuid"], "ROBOT-1")
                self.assertEqual(body["accountName"], "robot@example")
                self.assertEqual(body["idempotentUuid"], "ATTEMPT-OPENAPI-1")
                params = {item["name"]: item["value"] for item in body["params"]}
                self.assertEqual(params["execution_attempt_id"], "ATTEMPT-OPENAPI-1")
                self.assertEqual(params["target_price"], "19.50")
                self.assertEqual(params["product_sku"], "SKU-AISHA-C")
                request_json = json.loads(params["request_json"])
                self.assertEqual(request_json["execution_mode"], EXECUTION_MODE_COMMIT)
                return {"success": True, "code": 200, "data": {"jobUuid": "JOB-1", "idempotentFlag": False}}
            if url == "https://api.example.test/oapi/dispatch/v2/job/query":
                body = json.loads(request.data.decode("utf-8"))
                self.assertEqual(body["jobUuid"], "JOB-1")
                return {
                    "success": True,
                    "code": 200,
                    "data": {
                        "jobUuid": "JOB-1",
                        "status": "success",
                        "statusName": "运行成功",
                        "robotUuid": "ROBOT-1",
                        "robotParams": {"outputs": []},
                    },
                }
            if url == "https://api.example.test/oapi/robot/v2/queryRobotParam?robotUuid=ROBOT-1":
                self.assertEqual(request.get_method(), "GET")
                return {
                    "success": True,
                    "code": 200,
                    "data": [
                        {
                            "inputParams": [{"name": "request_json", "type": "str"}],
                            "outputParams": [{"name": "shadowbot_result_json", "type": "str"}],
                        }
                    ],
                }
            self.fail(f"unexpected request URL: {url}")

        runner = YingdaoOpenApiJobRunner(
            base_url="https://api.example.test",
            access_key_id="AK",
            access_key_secret="SK",
            robot_uuid="ROBOT-1",
            account_name="robot@example",
            http_requester=fake_request,
        )

        result = runner.start(
            {
                "operation_id": "OP-OPENAPI-1",
                "task_id": "TASK-SB-1",
                "platform": "蚂蚁花团供应商",
                "product_identity": {"sku": "SKU-AISHA-C", "name": "艾莎", "grade": "C级"},
                "expected_old_price": "19.00",
                "target_price": "19.50",
                "execution_attempt_id": "ATTEMPT-OPENAPI-1",
                "execution_mode": EXECUTION_MODE_COMMIT,
            }
        )

        self.assertEqual(result.shadowbot_run_id, "yingdao-job:JOB-1")
        self.assertEqual(len(requests), 2)
        self.assertEqual(result.raw_output["runner"], "yingdao_openapi_job")
        self.assertEqual(result.raw_output["jobUuid"], "JOB-1")
        self.assertEqual(result.raw_output["request"]["params"][0]["name"], "request_json")

        query_response = runner.query_job("JOB-1")
        self.assertEqual(query_response["data"]["jobUuid"], "JOB-1")
        param_response = runner.query_robot_params()
        self.assertEqual(param_response["data"][0]["inputParams"][0]["name"], "request_json")
        self.assertEqual(len(requests), 6)

    def test_check_yingdao_app_params_reports_missing_required_output(self) -> None:
        response = {
            "success": True,
            "code": 200,
            "data": [
                {
                    "inputParams": [{"name": "request_json", "type": "str"}],
                    "outputParams": [{"name": "other_output", "type": "str"}],
                }
            ],
        }

        result = check_yingdao_app_params(response)

        self.assertFalse(result["ok"])
        self.assertEqual(result["missing_inputs"], [])
        self.assertEqual(result["missing_outputs"], ["shadowbot_result_json"])

    def test_shadowbot_runner_factory_selects_yingdao_openapi_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SHADOWBOT_RUNNER_TYPE": "yingdao_openapi",
                "YINGDAO_ACCESS_KEY_ID": "AK",
                "YINGDAO_ACCESS_KEY_SECRET": "SK",
                "YINGDAO_ROBOT_UUID": "ROBOT-1",
                "YINGDAO_ACCOUNT_NAME": "robot@example",
            },
        ):
            runner = build_shadowbot_task_runner_from_environment()

        self.assertIsInstance(runner, YingdaoOpenApiJobRunner)

    def test_confirm_manual_handled_marks_operation_and_writes_audit_log(self) -> None:
        self.executor.start_execution(
            ShadowBotExecutionRequest(
                operation_id="OP-1",
                execution_attempt_id="ATTEMPT-1",
                execution_mode=EXECUTION_MODE_COMMIT,
                approval=_approval(),
            )
        )
        self.executor.record_result(
            ShadowBotResultContract(
                execution_attempt_id="ATTEMPT-1",
                status=STATUS_FAILED,
                run_success_flag=False,
                business_operation_completed=False,
                side_effect_state=SIDE_EFFECT_NOT_STARTED,
                retryable=False,
                error_code="PRODUCT_NOT_FOUND",
                **_lease_binding(self.repository, "ATTEMPT-1"),
            )
        )

        self.executor.confirm_manual_handled(operation_id="OP-1", actor="alice", note="checked")

        operation = self.repository.get_shadowbot_operation("OP-1")
        logs = self.repository.list_execution_logs(task_id="TASK-SB-1")
        self.assertEqual(operation.status, "MANUAL_HANDLED")
        self.assertTrue(any('"manual_actor": "alice"' in log.raw_output for log in logs))

    def test_shadowbot_executor_cli_start_writes_request_file(self) -> None:
        request_dir = self.temp_path / "requests"
        self.repository.insert_review_tasks([_review_task_for_payload("APPROVAL-CLI-1", _approved_payload("OP-CLI-1"))])

        result = start_from_args(
            Namespace(
                runtime_db=self.db_path,
                approval_id="APPROVAL-CLI-1",
                operation_id="OP-CLI-1",
                execution_attempt_id="ATTEMPT-CLI-1",
                execution_mode=EXECUTION_MODE_COMMIT,
                platform="蚂蚁花团供应商",
                sku="SKU-AISHA-C",
                product_name="艾莎",
                grade="C级",
                expected_old_price="19.00",
                target_price="19.50",
                request_dir=request_dir,
                runner_command="",
                runner_type="filedrop",
            )
        )

        request_file = request_dir / "inbox" / "ATTEMPT-CLI-1.ready.json"
        self.assertEqual(result.shadowbot_run_id, "filequeue:ATTEMPT-CLI-1")
        self.assertTrue(request_file.exists())
        request_payload = json.loads(request_file.read_text(encoding="utf-8"))
        self.assertEqual(request_payload["execution_mode"], EXECUTION_MODE_COMMIT)
        self.assertEqual(request_payload["operation_id"], "OP-CLI-1")

    def test_shadowbot_executor_cli_import_result_updates_runtime(self) -> None:
        request_dir = self.temp_path / "requests"
        self.repository.insert_review_tasks([_review_task_for_payload("APPROVAL-CLI-2", _approved_payload("OP-CLI-2"))])
        start_from_args(
            Namespace(
                runtime_db=self.db_path,
                approval_id="APPROVAL-CLI-2",
                operation_id="OP-CLI-2",
                execution_attempt_id="ATTEMPT-CLI-2",
                execution_mode=EXECUTION_MODE_COMMIT,
                platform="蚂蚁花团供应商",
                sku="SKU-AISHA-C",
                product_name="艾莎",
                grade="C级",
                expected_old_price="19.00",
                target_price="19.50",
                request_dir=request_dir,
                runner_command="",
                runner_type="filedrop",
            )
        )
        result_path = self.temp_path / "shadowbot_result.json"
        result_path.write_text(
            json.dumps(
                {
                    "execution_attempt_id": "ATTEMPT-CLI-2",
                    **_result_binding(self.repository, "ATTEMPT-CLI-2"),
                    "status": STATUS_VERIFIED,
                    "run_success_flag": True,
                    "business_operation_completed": True,
                    "side_effect_state": "VERIFIED",
                    "retryable": False,
                    "old_price": "19.00",
                    "target_price": "19.50",
                    "actual_price": "19.50",
                    "evidence_status": "COMPLETE",
                    "evidence": [{"type": "AFTER_SUBMIT", "storage_uri": r"\\share\after.png"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        record_result_from_file(self.db_path, result_path)

        operation = self.repository.get_shadowbot_operation("OP-CLI-2")
        task = self.repository.get_task("TASK-SB-1")
        logs = self.repository.list_execution_logs(task_id="TASK-SB-1")
        self.assertEqual(operation.status, STATUS_VERIFIED)
        self.assertEqual(task.task_status, TaskStatus.SUCCESS)
        self.assertTrue(any('"actual_price": "19.50"' in log.raw_output for log in logs))

    def test_shadowbot_executor_imports_result_from_yingdao_job_output_param(self) -> None:
        request_dir = self.temp_path / "requests"
        self.repository.insert_review_tasks([_review_task_for_payload("APPROVAL-CLI-3", _approved_payload("OP-CLI-3"))])
        start_from_args(
            Namespace(
                runtime_db=self.db_path,
                approval_id="APPROVAL-CLI-3",
                operation_id="OP-CLI-3",
                execution_attempt_id="ATTEMPT-CLI-3",
                execution_mode=EXECUTION_MODE_COMMIT,
                platform="蚂蚁花团供应商",
                sku="SKU-AISHA-C",
                product_name="艾莎",
                grade="C级",
                expected_old_price="19.00",
                target_price="19.50",
                request_dir=request_dir,
                runner_command="",
                runner_type="filedrop",
            )
        )
        shadowbot_result = {
            "execution_attempt_id": "ATTEMPT-CLI-3",
            **_result_binding(self.repository, "ATTEMPT-CLI-3"),
            "status": STATUS_VERIFIED,
            "run_success_flag": True,
            "business_operation_completed": True,
            "side_effect_state": "VERIFIED",
            "retryable": False,
            "old_price": "19.00",
            "target_price": "19.50",
            "actual_price": "19.50",
            "evidence_status": "COMPLETE",
        }
        job_query_response = {
            "success": True,
            "code": 200,
            "data": {
                "jobUuid": "JOB-CLI-3",
                "status": "success",
                "statusName": "运行成功",
                "robotUuid": "ROBOT-1",
                "robotName": "test2",
                "robotParams": {
                    "outputs": [
                        {
                            "name": "shadowbot_result_json",
                            "value": json.dumps(shadowbot_result, ensure_ascii=False),
                            "type": "str",
                        }
                    ]
                },
            },
        }

        result = record_result_from_yingdao_job_query(self.db_path, job_query_response)

        operation = self.repository.get_shadowbot_operation("OP-CLI-3")
        logs = self.repository.list_execution_logs(task_id="TASK-SB-1")
        self.assertEqual(result.execution_attempt_id, "ATTEMPT-CLI-3")
        self.assertEqual(operation.status, STATUS_VERIFIED)
        self.assertTrue(any('"jobUuid": "JOB-CLI-3"' in log.raw_output for log in logs))

    def test_prepare_shadowbot_chain_creates_update_price_task_and_approved_review(self) -> None:
        db_path = self.temp_path / "prepared_runtime.sqlite3"

        result = prepare_shadowbot_chain_from_args(
            Namespace(
                runtime_db=db_path,
                platform="蚂蚁花团供应商",
                sku="SKU-AISHA-C",
                product_name="艾莎",
                grade="C级",
                expected_old_price="19.00",
                target_price="19.50",
                task_id="TASK-PREP-1",
                approval_id="REVIEW-PREP-1",
                operation_id="OP-PREP-1",
                execution_attempt_id="ATTEMPT-PREP-1",
                trade_date="2026-06-26",
                approved_by="alice",
                approval_ttl_minutes=60,
                start=False,
            )
        )

        repository = SQLiteRuntimeRepository(db_path)
        task = repository.get_task("TASK-PREP-1")
        review = repository.get_review_task("REVIEW-PREP-1")
        self.assertFalse(result.started)
        self.assertEqual(task.action_type, TaskActionType.UPDATE_PRICE)
        self.assertEqual(task.task_status, TaskStatus.PENDING)
        self.assertEqual(task.target_price, Decimal("19.50"))
        self.assertEqual(review.review_status, ReviewTaskStatus.APPROVED)
        self.assertEqual(review.source_task_id, "TASK-PREP-1")
        self.assertEqual(review.resolution_payload["approved_payload_hash"], result.approved_payload_hash)

    def test_prepare_shadowbot_chain_can_start_filedrop_runner(self) -> None:
        db_path = self.temp_path / "prepared_start_runtime.sqlite3"
        request_dir = self.temp_path / "prepared_requests"
        with patch.dict(
            os.environ,
            {
                "SHADOWBOT_RUNNER_TYPE": "filedrop",
                "SHADOWBOT_REQUEST_DIR": str(request_dir),
            },
            clear=False,
        ):
            result = prepare_shadowbot_chain_from_args(
                Namespace(
                    runtime_db=db_path,
                    platform="蚂蚁花团供应商",
                    sku="SKU-AISHA-C",
                    product_name="艾莎",
                    grade="C级",
                    expected_old_price="19.00",
                    target_price="19.50",
                    task_id="TASK-PREP-START-1",
                    approval_id="REVIEW-PREP-START-1",
                    operation_id="OP-PREP-START-1",
                    execution_attempt_id="ATTEMPT-PREP-START-1",
                    trade_date="2026-06-26",
                    approved_by="alice",
                    approval_ttl_minutes=60,
                    start=True,
                )
            )

        repository = SQLiteRuntimeRepository(db_path)
        operation = repository.get_shadowbot_operation("OP-PREP-START-1")
        attempt = repository.get_shadowbot_execution_attempt("ATTEMPT-PREP-START-1")
        attempts = repository.list_shadowbot_execution_attempts(operation_id="OP-PREP-START-1")
        request_file = request_dir / "inbox" / "ATTEMPT-PREP-START-1.ready.json"
        self.assertTrue(result.started)
        self.assertEqual(result.shadowbot_run_id, "filequeue:ATTEMPT-PREP-START-1")
        self.assertEqual(operation.status, STATUS_RUNNING)
        self.assertEqual(attempt.execution_mode, EXECUTION_MODE_COMMIT)
        self.assertEqual([item.execution_attempt_id for item in attempts], ["ATTEMPT-PREP-START-1"])
        self.assertTrue(request_file.exists())

    def test_local_demo_covers_three_shadowbot_result_branches(self) -> None:
        db_path = self.temp_path / "demo_runtime.sqlite3"
        request_dir = self.temp_path / "demo_requests"

        summary = run_local_demo_from_args(
            Namespace(
                runtime_db=db_path,
                request_dir=request_dir,
                platform="蚂蚁花团供应商",
                sku="SKU-AISHA-C",
                product_name="艾莎",
                grade="C级",
                expected_old_price="19.00",
                target_price="19.50",
                trade_date="2026-06-26",
            )
        )

        repository = SQLiteRuntimeRepository(db_path)
        success_operation = repository.get_shadowbot_operation("OP-DEMO-SUCCESS")
        pre_fail_operation = repository.get_shadowbot_operation("OP-DEMO-PREFAIL")
        unknown_operation = repository.get_shadowbot_operation("OP-DEMO-UNKNOWN")
        reconcile_id = "RECONCILE-" + hashlib.sha256(b"ATTEMPT-DEMO-UNKNOWN").hexdigest()[:20]
        reconcile_attempt = repository.get_shadowbot_execution_attempt(reconcile_id)
        success_task = repository.get_task("TASK-DEMO-SUCCESS")
        pre_fail_task = repository.get_task("TASK-DEMO-PREFAIL")
        logs = repository.list_execution_logs()
        request_files = sorted(path.name for path in (request_dir / "inbox").glob("*.ready.json"))
        self.assertEqual(success_operation.status, STATUS_VERIFIED)
        self.assertEqual(pre_fail_operation.status, STATUS_FAILED)
        self.assertEqual(unknown_operation.status, STATUS_NOT_APPLIED)
        self.assertEqual(success_task.task_status, TaskStatus.SUCCESS)
        self.assertEqual(pre_fail_task.task_status, TaskStatus.FAILED)
        self.assertEqual(reconcile_attempt.execution_mode, EXECUTION_MODE_RECONCILE)
        self.assertIn(f"{reconcile_id}.ready.json", request_files)
        self.assertEqual(summary["reconcile"]["execution_attempt_id"], reconcile_id)
        self.assertTrue(any('"status": "SIDE_EFFECT_UNKNOWN"' in log.raw_output for log in logs))
        self.assertTrue(any('"status": "NOT_APPLIED"' in log.raw_output for log in logs))

    def test_readiness_report_accepts_complete_openapi_config_and_redacts_secrets(self) -> None:
        db_path = self.temp_path / "readiness.sqlite3"
        SQLiteRuntimeRepository(db_path).init_schema()

        with patch.dict(
            os.environ,
            {
                "SHADOWBOT_RUNNER_TYPE": "yingdao_openapi",
                "YINGDAO_API_BASE_URL": "https://openapi.example.test",
                "YINGDAO_ACCESS_KEY_ID": "secret-id-value",
                "YINGDAO_ACCESS_KEY_SECRET": "secret-key-value",
                "YINGDAO_ROBOT_UUID": "robot-1",
                "YINGDAO_ACCOUNT_NAME": "robot-account",
                "YINGDAO_ROBOT_CLIENT_GROUP_UUID": "group-1",
            },
            clear=True,
        ):
            report = build_readiness_report(db_path)

        encoded = json.dumps(report, ensure_ascii=False)
        self.assertTrue(report["ok"])
        self.assertNotIn("allowed_skus_configured", encoded)
        self.assertNotIn("allowed_platforms_configured", encoded)
        self.assertIn('"length": 15', encoded)
        self.assertIn('"length": 16', encoded)
        self.assertNotIn("secret-id-value", encoded)
        self.assertNotIn("secret-key-value", encoded)


if __name__ == "__main__":
    unittest.main()

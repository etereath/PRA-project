from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from app.enums import (
    ReviewTaskStatus,
    TaskActionType,
    TaskOriginType,
    TaskStatus,
)
from app.exceptions import ValidationError
from app.models import ReviewTask, ShadowBotExecutionAttempt, ShadowBotOperationLedger, Task
from app.repositories.sqlite_connection import SQLiteConnectionConfig
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.runtime import RuntimeTaskService
from app.services.shadowbot_executor import (
    EXECUTION_MODE_COMMIT,
    EXECUTION_MODE_READ_ONLY,
    SIDE_EFFECT_NOT_APPLIED,
    SIDE_EFFECT_NOT_STARTED,
    SIDE_EFFECT_UNKNOWN,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SIDE_EFFECT_UNKNOWN,
    STATUS_START_FAILED,
    STATUS_START_UNKNOWN,
    STATUS_VERIFIED,
    ShadowBotApproval,
    ShadowBotApprovedPayload,
    ShadowBotExecutionRequest,
    ShadowBotExecutor,
    ShadowBotFileQueueRunner,
    ShadowBotResultContract,
    ShadowBotStartBoundaryError,
    ShadowBotStartResult,
    compute_approved_payload_hash,
    shadowbot_result_contract_from_data,
)
from app.services.shadowbot_queue import ShadowBotQueueWatchdog, ShadowBotResultImporter
from app.services.shadowbot_recovery import (
    EVIDENCE_NOT_APPLIED_RESULT,
    EVIDENCE_PRE_PUBLISH_NOT_PUBLISHED,
    RetryEvidence,
    RetryPolicyService,
    ShadowBotLeaseWatchdog,
)
from app.services.shadowbot_state import OperationStatus


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def start(self, payload: dict[str, object]) -> ShadowBotStartResult:
        self.calls.append(payload)
        return ShadowBotStartResult(shadowbot_run_id=f"RUN-{len(self.calls)}", raw_output={"accepted": True})


class BoundaryRunner:
    def __init__(self, *, published: bool, raw_output: dict[str, object] | None = None) -> None:
        self.published = published
        self.raw_output = raw_output or {}
        self.calls = 0

    def start(self, payload: dict[str, object]) -> ShadowBotStartResult:
        del payload
        self.calls += 1
        raise ShadowBotStartBoundaryError(
            "controlled start boundary failure",
            published=self.published,
            raw_output=self.raw_output,
        )


def _task() -> Task:
    return Task(
        task_id="TASK-RECOVERY",
        internal_sku="SKU-RECOVERY",
        platform_name="测试平台",
        action_type=TaskActionType.UPDATE_PRICE,
        priority=1,
        task_status=TaskStatus.PENDING,
        created_at=datetime(2026, 7, 15, 8, 0),
        origin_type=TaskOriginType.MANUAL,
        origin_ref_id="test-harness:shadowbot-recovery:TASK-RECOVERY",
        target_price=Decimal("19.50"),
        trade_date=date(2026, 7, 15),
        scope_type="sku",
        scope_key="SKU-RECOVERY",
        dedupe_key="TASK-RECOVERY|update_price",
    )


def _payload(operation_id: str = "OP-RECOVERY") -> ShadowBotApprovedPayload:
    return ShadowBotApprovedPayload(
        operation_id=operation_id,
        task_id="TASK-RECOVERY",
        platform="测试平台",
        product_identity={
            "internal_sku": "SKU-RECOVERY",
            "platform_sku": "SKU-RECOVERY",
            "name": "测试商品",
            "grade": "A",
        },
        expected_old_price=Decimal("19.00"),
        target_price=Decimal("19.50"),
    )


def _approval(operation_id: str = "OP-RECOVERY") -> ShadowBotApproval:
    payload = _payload(operation_id)
    return ShadowBotApproval(
        approval_id="APPROVAL-RECOVERY",
        approval_status="APPROVED",
        approved_payload=payload,
        approved_payload_hash=compute_approved_payload_hash(payload),
        approved_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _repository(
    tmp_path: Path,
    operation_id: str = "OP-RECOVERY",
    *,
    connection_config: SQLiteConnectionConfig | None = None,
    clock=None,
    retry_sleep=None,
) -> SQLiteRuntimeRepository:
    repository = SQLiteRuntimeRepository(
        tmp_path / "runtime.sqlite3",
        connection_config=connection_config,
        clock=clock,
        retry_sleep=retry_sleep,
    )
    RuntimeTaskService(repository).init_schema()
    RuntimeTaskService(repository).create_tasks([_task()])
    payload = _payload(operation_id)
    approved_hash = compute_approved_payload_hash(payload)
    repository.insert_review_tasks(
        [
            ReviewTask(
                review_task_id="APPROVAL-RECOVERY",
                trade_date=date(2026, 7, 15),
                scope_type="sku",
                scope_key="SKU-RECOVERY",
                dedupe_key="APPROVAL-RECOVERY",
                source_task_id="TASK-RECOVERY",
                review_type="price_update",
                review_status=ReviewTaskStatus.APPROVED,
                internal_sku="SKU-RECOVERY",
                platform_name="测试平台",
                reason="controlled test approval",
                review_payload={"approved_payload_hash": approved_hash},
                resolution_payload={"approved_payload_hash": approved_hash},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                resolved_by="tester",
                resolved_at=datetime.now(UTC),
            )
        ]
    )
    return repository


def _start_request(
    *,
    attempt_id: str,
    retry_authorization_id: str = "",
    operation_id: str = "OP-RECOVERY",
) -> ShadowBotExecutionRequest:
    return ShadowBotExecutionRequest(
        operation_id=operation_id,
        execution_attempt_id=attempt_id,
        execution_mode=EXECUTION_MODE_COMMIT,
        approval=_approval(operation_id),
        retry_authorization_id=retry_authorization_id,
        lease_seconds=900,
    )


def _lease(attempt: ShadowBotExecutionAttempt) -> dict[str, object]:
    return dict(attempt.raw_output["lease"])


def _lease_binding_for_contract(attempt: ShadowBotExecutionAttempt) -> dict[str, object]:
    lease = _lease(attempt)
    return {
        "lease_owner_token": str(lease["owner_token"]),
        "lease_version": int(lease["version"]),
    }


def _write_json_with_checksum(path: Path, payload: dict[str, object], *, bad_checksum: bool = False) -> None:
    content = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    checksum = "0" * 64 if bad_checksum else hashlib.sha256(content).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(checksum + "\n", encoding="ascii")


def _file_attempt(
    tmp_path: Path,
    *,
    attempt_id: str = "ATTEMPT-FILE",
    connection_config: SQLiteConnectionConfig | None = None,
    clock=None,
    retry_sleep=None,
):
    repository = _repository(
        tmp_path,
        connection_config=connection_config,
        clock=clock,
        retry_sleep=retry_sleep,
    )
    queue_dir = tmp_path / "queue"
    runner = ShadowBotFileQueueRunner(queue_dir)
    executor = ShadowBotExecutor(repository, runner)
    executor.start_execution(_start_request(attempt_id=attempt_id))
    attempt = repository.get_shadowbot_execution_attempt(attempt_id)
    assert attempt is not None
    request_path = queue_dir / "inbox" / f"{attempt_id}.ready.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    return repository, queue_dir, runner, executor, attempt, request_path, request


def _result_payload(attempt: ShadowBotExecutionAttempt, request: dict[str, object], *, status: str = STATUS_VERIFIED):
    lease = _lease(attempt)
    if status == STATUS_VERIFIED:
        flags = (True, True, "VERIFIED", "")
    elif status == STATUS_FAILED:
        flags = (False, False, SIDE_EFFECT_NOT_APPLIED, "CONTROLLED_FAILURE")
    else:
        flags = (None, None, SIDE_EFFECT_UNKNOWN, "CONTROLLED_UNKNOWN")
    return {
        "schema_version": "shadowbot-result-1.0",
        "result_id": f"RESULT-{attempt.execution_attempt_id}-{status}",
        "task_id": request["task_id"],
        "operation_id": request["operation_id"],
        "execution_attempt_id": attempt.execution_attempt_id,
        "execution_mode": request["execution_mode"],
        "instruction_hash": request["instruction_hash"],
        "request_file_sha256": attempt.request_file_sha256,
        "lease_owner_token": lease["owner_token"],
        "lease_version": lease["version"],
        "worker_id": "worker-test",
        "status": status,
        "run_success_flag": flags[0],
        "business_operation_completed": flags[1],
        "side_effect_state": flags[2],
        "error_code": flags[3],
        "retryable": False,
    }


def _pre_publish_evidence(attempt: ShadowBotExecutionAttempt, *, result_id: str) -> RetryEvidence:
    return RetryEvidence(
        evidence_type=EVIDENCE_PRE_PUBLISH_NOT_PUBLISHED,
        result_id=result_id,
        instruction_hash=attempt.instruction_hash,
        request_file_sha256=attempt.request_file_sha256,
        approved_payload_hash=_approval().approved_payload_hash,
        side_effect_state=SIDE_EFFECT_NOT_STARTED,
        checksum_valid=True,
        ready_published=False,
        worker_claimed=False,
        platform_action=False,
    )


def test_f01_before_temp_write_is_start_failed_and_requires_authorization(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    executor = ShadowBotExecutor(repository, BoundaryRunner(published=False))
    with pytest.raises(ShadowBotStartBoundaryError):
        executor.start_execution(_start_request(attempt_id="ATTEMPT-F01"))
    attempt = repository.get_shadowbot_execution_attempt("ATTEMPT-F01")
    assert attempt.status == STATUS_START_FAILED
    assert repository.get_shadowbot_operation("OP-RECOVERY").status == OperationStatus.FAILED.value
    with pytest.raises(ValidationError, match="RETRY_AUTHORIZATION_REQUIRED"):
        ShadowBotExecutor(repository, RecordingRunner()).start_execution(_start_request(attempt_id="ATTEMPT-F01-B"))


def test_f02_temp_write_failure_records_unpublished_evidence(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    runner = BoundaryRunner(published=False, raw_output={"temporary_artifact": "cleaned"})
    with pytest.raises(ShadowBotStartBoundaryError):
        ShadowBotExecutor(repository, runner).start_execution(_start_request(attempt_id="ATTEMPT-F02"))
    attempt = repository.get_shadowbot_execution_attempt("ATTEMPT-F02")
    assert attempt.status == STATUS_START_FAILED
    assert attempt.raw_output["published"] is False
    assert attempt.raw_output["temporary_artifact"] == "cleaned"
    authorization = RetryPolicyService(repository).issue_automatic(
        source_execution_attempt_id=attempt.execution_attempt_id,
        evidence=RetryEvidence(
            evidence_type=EVIDENCE_PRE_PUBLISH_NOT_PUBLISHED,
            result_id="EVIDENCE-F02",
            instruction_hash=attempt.instruction_hash,
            request_file_sha256="",
            approved_payload_hash=_approval().approved_payload_hash,
            side_effect_state=SIDE_EFFECT_NOT_STARTED,
            checksum_valid=True,
            ready_published=False,
            worker_claimed=False,
            platform_action=False,
        ),
    )
    assert authorization.status == "ACTIVE"
    assert repository.get_shadowbot_operation("OP-RECOVERY").status == OperationStatus.RETRY_AUTHORIZED.value


def test_f03_ready_published_before_db_confirmation_is_start_unknown(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    with pytest.raises(ShadowBotStartBoundaryError):
        ShadowBotExecutor(repository, BoundaryRunner(published=True)).start_execution(
            _start_request(attempt_id="ATTEMPT-F03")
        )
    attempt = repository.get_shadowbot_execution_attempt("ATTEMPT-F03")
    assert attempt.status == STATUS_START_UNKNOWN
    assert repository.get_shadowbot_operation("OP-RECOVERY").status == OperationStatus.NEEDS_RECONCILIATION.value


def test_f04_claimed_without_not_applied_proof_is_start_unknown(tmp_path: Path) -> None:
    repository, queue_dir, _, _, attempt, _, request = _file_attempt(tmp_path, attempt_id="ATTEMPT-F04")
    watchdog = ShadowBotQueueWatchdog(queue_dir, repository=repository)
    event = watchdog._write_recovery_result(  # noqa: SLF001 - controlled matrix injection
        request,
        {"request_file_sha256": attempt.request_file_sha256, "side_effect_state": SIDE_EFFECT_NOT_STARTED},
        phase="CLAIMED",
    )
    data = json.loads(Path(event["result_path"]).read_text(encoding="utf-8"))
    assert data["status"] == STATUS_START_UNKNOWN
    assert data["retryable"] is False


def test_f05_not_applied_evidence_authorizes_once_and_creates_new_lease(tmp_path: Path) -> None:
    repository, queue_dir, runner, executor, source, _, request = _file_attempt(
        tmp_path, attempt_id="ATTEMPT-F05-SOURCE"
    )
    lease = _lease(source)
    executor.record_result(
        ShadowBotResultContract(
            execution_attempt_id=source.execution_attempt_id,
            status=STATUS_FAILED,
            run_success_flag=False,
            business_operation_completed=False,
            side_effect_state=SIDE_EFFECT_NOT_APPLIED,
            retryable=False,
            error_code="BEFORE_SUBMIT_FAILED",
            operation_id=source.operation_id,
            task_id="TASK-RECOVERY",
            execution_mode=EXECUTION_MODE_COMMIT,
            instruction_hash=source.instruction_hash,
            request_file_sha256=source.request_file_sha256,
            result_id="RESULT-F05",
            lease_owner_token=str(lease["owner_token"]),
            lease_version=int(lease["version"]),
            raw_output={"failed_phase": "PRICE_VERIFIED", "cleanup_confirmed": True},
        )
    )
    evidence = RetryEvidence(
        evidence_type=EVIDENCE_NOT_APPLIED_RESULT,
        result_id="RESULT-F05",
        instruction_hash=source.instruction_hash,
        request_file_sha256=source.request_file_sha256,
        approved_payload_hash=_approval().approved_payload_hash,
        side_effect_state=SIDE_EFFECT_NOT_APPLIED,
        checksum_valid=True,
        failed_phase="PRICE_VERIFIED",
    )
    authorization = RetryPolicyService(repository).issue_automatic(
        source_execution_attempt_id=source.execution_attempt_id,
        evidence=evidence,
    )

    def consume(attempt_id: str):
        try:
            return ShadowBotExecutor(repository, runner).start_execution(
                _start_request(attempt_id=attempt_id, retry_authorization_id=authorization.retry_authorization_id)
            )
        except ValidationError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(consume, ("ATTEMPT-F05-A", "ATTEMPT-F05-B")))
    successes = [item for item in outcomes if not isinstance(item, str)]
    assert len(successes) == 1
    retry_attempt = repository.get_shadowbot_execution_attempt(successes[0].execution_attempt_id)
    assert _lease(retry_attempt)["version"] > lease["version"]
    assert _lease(retry_attempt)["owner_token"] != lease["owner_token"]
    assert repository.get_retry_authorization(authorization.retry_authorization_id).status == "CONSUMED"
    assert (queue_dir / "archive" / source.execution_attempt_id).exists()


def test_task8_lease_renewal_retries_a_real_sqlite_lock(tmp_path: Path) -> None:
    config = SQLiteConnectionConfig(
        busy_timeout_ms=100,
        retry_max_attempts=3,
        retry_max_elapsed_ms=500,
        retry_base_delay_ms=1,
    )
    retry_started = threading.Event()
    release_retry_sleep = threading.Event()
    outcome: dict[str, object] = {}

    def retry_sleep(_seconds: float) -> None:
        retry_started.set()
        assert release_retry_sleep.wait(timeout=2)

    repository, _, _, _, attempt, _, _ = _file_attempt(
        tmp_path,
        connection_config=config,
        retry_sleep=retry_sleep,
    )
    lease = _lease(attempt)
    blocker = repository.connect()
    blocker.execute("BEGIN IMMEDIATE")
    started = threading.Event()

    def renew() -> None:
        started.set()
        try:
            outcome["result"] = repository.renew_shadowbot_lease(
                attempt.execution_attempt_id,
                owner_token=str(lease["owner_token"]),
                lease_version=int(lease["version"]),
                lease_seconds=1200,
            )
        except BaseException as exc:  # noqa: BLE001 - preserve thread failure for the assertion
            outcome["error"] = exc

    worker = threading.Thread(target=renew)
    worker.start()
    assert started.wait(timeout=2)
    assert retry_started.wait(timeout=2)
    blocker.rollback()
    blocker.close()
    release_retry_sleep.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert "error" not in outcome, outcome.get("error")
    assert outcome.get("result") is True
    renewed = repository.get_shadowbot_execution_attempt(attempt.execution_attempt_id)
    assert renewed is not None
    assert _lease(renewed)["expires_at"] != lease["expires_at"]


def test_task8_lease_renewal_rechecks_time_after_lock_wait(tmp_path: Path) -> None:
    base = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    retry_started = threading.Event()
    release_retry_sleep = threading.Event()

    def retry_sleep(_seconds: float) -> None:
        retry_started.set()
        assert release_retry_sleep.wait(timeout=2)

    repository, _, _, _, attempt, _, _ = _file_attempt(
        tmp_path,
        connection_config=SQLiteConnectionConfig(
            busy_timeout_ms=100,
            retry_max_attempts=3,
            retry_max_elapsed_ms=500,
            retry_base_delay_ms=1,
        ),
        clock=lambda: base + timedelta(seconds=2),
        retry_sleep=retry_sleep,
    )
    lease = _lease(attempt)
    connection = repository.connect()
    try:
        raw = json.loads(
            connection.execute(
                "SELECT raw_output_json FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?",
                (attempt.execution_attempt_id,),
            ).fetchone()[0]
        )
        raw["lease"]["expires_at"] = (base + timedelta(seconds=1)).isoformat()
        connection.execute(
            "UPDATE shadowbot_execution_attempts SET raw_output_json = ? WHERE execution_attempt_id = ?",
            (json.dumps(raw), attempt.execution_attempt_id),
        )
        connection.commit()
    finally:
        connection.close()

    blocker = repository.connect()
    blocker.execute("BEGIN IMMEDIATE")
    outcome: dict[str, object] = {}

    def renew() -> None:
        try:
            outcome["result"] = repository.renew_shadowbot_lease(
                attempt.execution_attempt_id,
                owner_token=str(lease["owner_token"]),
                lease_version=int(lease["version"]),
                lease_seconds=900,
            )
        except BaseException as exc:  # noqa: BLE001 - preserve thread failure for the assertion
            outcome["error"] = exc

    worker = threading.Thread(target=renew)
    worker.start()
    assert retry_started.wait(timeout=2)
    blocker.rollback()
    blocker.close()
    release_retry_sleep.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert "error" not in outcome, outcome.get("error")
    assert outcome.get("result") is False
    unchanged = repository.get_shadowbot_execution_attempt(attempt.execution_attempt_id)
    assert unchanged is not None
    assert _lease(unchanged)["expires_at"] == (base + timedelta(seconds=1)).isoformat()


def test_manual_retry_authorization_records_actor_reason_and_evidence(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    with pytest.raises(ShadowBotStartBoundaryError):
        ShadowBotExecutor(repository, BoundaryRunner(published=False)).start_execution(
            _start_request(attempt_id="ATTEMPT-MANUAL-SOURCE")
        )
    source = repository.get_shadowbot_execution_attempt("ATTEMPT-MANUAL-SOURCE")
    evidence = RetryEvidence(
        evidence_type=EVIDENCE_PRE_PUBLISH_NOT_PUBLISHED,
        result_id="EVIDENCE-MANUAL",
        instruction_hash=source.instruction_hash,
        request_file_sha256="",
        approved_payload_hash=_approval().approved_payload_hash,
        side_effect_state=SIDE_EFFECT_NOT_STARTED,
        checksum_valid=True,
        ready_published=False,
        worker_claimed=False,
        platform_action=False,
    )
    with pytest.raises(ValidationError, match="ACTOR_AND_REASON"):
        RetryPolicyService(repository).issue_manual(
            source_execution_attempt_id=source.execution_attempt_id,
            actor="",
            reason="",
            evidence=evidence,
        )
    authorization = RetryPolicyService(repository).issue_manual(
        source_execution_attempt_id=source.execution_attempt_id,
        actor="operator-a",
        reason="controlled manual approval",
        evidence=evidence,
    )
    assert authorization.authorization_type == "MANUAL"
    assert authorization.authorized_by == "operator-a"
    assert authorization.reason == "controlled manual approval"


def test_retry_authorization_rejects_expired_source_approval(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    with pytest.raises(ShadowBotStartBoundaryError):
        ShadowBotExecutor(repository, BoundaryRunner(published=False)).start_execution(
            _start_request(attempt_id="ATTEMPT-APPROVAL-EXPIRED")
        )
    source = repository.get_shadowbot_execution_attempt("ATTEMPT-APPROVAL-EXPIRED")
    raw = dict(source.raw_output)
    raw["approval_expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    repository.update_shadowbot_execution_attempt(source.execution_attempt_id, raw_output=raw)
    source = repository.get_shadowbot_execution_attempt(source.execution_attempt_id)
    with pytest.raises(ValidationError, match="RETRY_SOURCE_APPROVAL_EXPIRED"):
        RetryPolicyService(repository).issue_automatic(
            source_execution_attempt_id=source.execution_attempt_id,
            evidence=_pre_publish_evidence(source, result_id="EVIDENCE-APPROVAL-EXPIRED"),
        )


def test_retry_authorization_rejects_expired_total_time_window(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    with pytest.raises(ShadowBotStartBoundaryError):
        ShadowBotExecutor(repository, BoundaryRunner(published=False)).start_execution(
            _start_request(attempt_id="ATTEMPT-WINDOW-EXPIRED")
        )
    source = repository.get_shadowbot_execution_attempt("ATTEMPT-WINDOW-EXPIRED")
    with pytest.raises(ValidationError, match="RETRY_TOTAL_TIME_WINDOW_EXPIRED"):
        RetryPolicyService(repository, max_retry_window=timedelta(minutes=5)).issue_automatic(
            source_execution_attempt_id=source.execution_attempt_id,
            evidence=_pre_publish_evidence(source, result_id="EVIDENCE-WINDOW-EXPIRED"),
            now=source.started_at + timedelta(minutes=6),
        )


def test_retry_authorization_accepts_valid_approval_inside_total_window(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    with pytest.raises(ShadowBotStartBoundaryError):
        ShadowBotExecutor(repository, BoundaryRunner(published=False)).start_execution(
            _start_request(attempt_id="ATTEMPT-WINDOW-VALID")
        )
    source = repository.get_shadowbot_execution_attempt("ATTEMPT-WINDOW-VALID")
    authorization = RetryPolicyService(repository, max_retry_window=timedelta(minutes=5)).issue_automatic(
        source_execution_attempt_id=source.execution_attempt_id,
        evidence=_pre_publish_evidence(source, result_id="EVIDENCE-WINDOW-VALID"),
        now=source.started_at + timedelta(minutes=4),
    )
    assert authorization.status == "ACTIVE"
    assert authorization.expires_at <= datetime.fromisoformat(str(source.raw_output["approval_expires_at"]))


def test_retry_authorization_cannot_be_consumed_after_total_window(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    with pytest.raises(ShadowBotStartBoundaryError):
        ShadowBotExecutor(repository, BoundaryRunner(published=False)).start_execution(
            _start_request(attempt_id="ATTEMPT-WINDOW-CONSUME-LATE")
        )
    source = repository.get_shadowbot_execution_attempt("ATTEMPT-WINDOW-CONSUME-LATE")
    operation = repository.get_shadowbot_operation(source.operation_id)
    approval_expiry = datetime.fromisoformat(str(source.raw_output["approval_expires_at"]))
    retry_window_deadline = min(operation.created_at, source.started_at) + timedelta(minutes=5)
    retry_window_deadline = min(retry_window_deadline, approval_expiry)
    authorization = RetryPolicyService(
        repository,
        authorization_ttl=timedelta(minutes=15),
        max_retry_window=timedelta(minutes=5),
    ).issue_automatic(
        source_execution_attempt_id=source.execution_attempt_id,
        evidence=_pre_publish_evidence(source, result_id="EVIDENCE-WINDOW-CONSUME-LATE"),
        now=retry_window_deadline - timedelta(seconds=10),
    )
    assert authorization.expires_at == retry_window_deadline
    source = repository.get_shadowbot_execution_attempt(source.execution_attempt_id)
    assert datetime.fromisoformat(str(source.raw_output["retry_window_deadline"])) == retry_window_deadline
    assert source.raw_output["max_retry_window_seconds"] == 300

    # Fault-inject a longer ordinary TTL: consumption must still reject the persisted/recomputed window.
    with repository.connect() as connection:
        connection.execute(
            "UPDATE retry_authorizations SET expires_at = ? WHERE retry_authorization_id = ?",
            (
                (retry_window_deadline + timedelta(minutes=10)).isoformat(),
                authorization.retry_authorization_id,
            ),
        )
        connection.commit()
    with patch(
        "app.services.shadowbot_executor.utc_now",
        return_value=retry_window_deadline + timedelta(seconds=1),
    ):
        with pytest.raises(ValidationError, match="RETRY_AUTHORIZATION_CONSUME_CONFLICT"):
            ShadowBotExecutor(repository, RecordingRunner()).start_execution(
                _start_request(
                    attempt_id="ATTEMPT-WINDOW-CONSUME-LATE-RETRY",
                    retry_authorization_id=authorization.retry_authorization_id,
                )
            )
    assert repository.get_retry_authorization(authorization.retry_authorization_id).status == "ACTIVE"
    assert repository.get_shadowbot_execution_attempt("ATTEMPT-WINDOW-CONSUME-LATE-RETRY") is None
    assert repository.get_shadowbot_operation(source.operation_id).status == OperationStatus.RETRY_AUTHORIZED.value


def test_retry_authorization_consumes_inside_total_window(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    with pytest.raises(ShadowBotStartBoundaryError):
        ShadowBotExecutor(repository, BoundaryRunner(published=False)).start_execution(
            _start_request(attempt_id="ATTEMPT-WINDOW-CONSUME-VALID")
        )
    source = repository.get_shadowbot_execution_attempt("ATTEMPT-WINDOW-CONSUME-VALID")
    operation = repository.get_shadowbot_operation(source.operation_id)
    approval_expiry = datetime.fromisoformat(str(source.raw_output["approval_expires_at"]))
    retry_window_deadline = min(
        min(operation.created_at, source.started_at) + timedelta(minutes=5),
        approval_expiry,
    )
    authorization = RetryPolicyService(
        repository,
        max_retry_window=timedelta(minutes=5),
    ).issue_automatic(
        source_execution_attempt_id=source.execution_attempt_id,
        evidence=_pre_publish_evidence(source, result_id="EVIDENCE-WINDOW-CONSUME-VALID"),
        now=retry_window_deadline - timedelta(seconds=10),
    )
    with patch(
        "app.services.shadowbot_executor.utc_now",
        return_value=retry_window_deadline - timedelta(seconds=1),
    ):
        result = ShadowBotExecutor(repository, RecordingRunner()).start_execution(
            _start_request(
                attempt_id="ATTEMPT-WINDOW-CONSUME-VALID-RETRY",
                retry_authorization_id=authorization.retry_authorization_id,
            )
        )
    assert result.status == STATUS_RUNNING
    assert repository.get_retry_authorization(authorization.retry_authorization_id).status == "CONSUMED"


def test_retryable_hint_never_replaces_retry_authorization(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    executor = ShadowBotExecutor(repository, RecordingRunner())
    executor.start_execution(_start_request(attempt_id="ATTEMPT-RETRYABLE-HINT"))
    attempt = repository.get_shadowbot_execution_attempt("ATTEMPT-RETRYABLE-HINT")
    executor.record_result(
        ShadowBotResultContract(
            execution_attempt_id=attempt.execution_attempt_id,
            status=STATUS_FAILED,
            run_success_flag=False,
            business_operation_completed=False,
            side_effect_state=SIDE_EFFECT_NOT_STARTED,
            retryable=True,
            error_code="RETRYABLE_HINT_ONLY",
            operation_id=attempt.operation_id,
            task_id="TASK-RECOVERY",
            execution_mode=EXECUTION_MODE_COMMIT,
            instruction_hash=attempt.instruction_hash,
            **_lease_binding_for_contract(attempt),
        )
    )
    with pytest.raises(ValidationError, match="RETRY_AUTHORIZATION_REQUIRED"):
        executor.start_execution(_start_request(attempt_id="ATTEMPT-RETRYABLE-HINT-2"))


def test_legacy_result_and_side_effect_aliases_are_read_only_normalized() -> None:
    contract = shadowbot_result_contract_from_data(
        {
            "execution_attempt_id": "LEGACY-RESULT",
            "status": "NEEDS_RECONCILIATION",
            "run_success_flag": None,
            "business_operation_completed": None,
            "side_effect_state": "UNKNOWN",
            "retryable": False,
        }
    )
    assert contract.status == STATUS_SIDE_EFFECT_UNKNOWN
    assert contract.raw_output["legacy_state_normalization"]["result_status"] == "NEEDS_RECONCILIATION"

    not_applied = shadowbot_result_contract_from_data(
        {
            "execution_attempt_id": "LEGACY-SIDE-EFFECT",
            "status": "NOT_APPLIED",
            "run_success_flag": True,
            "business_operation_completed": False,
            "side_effect_state": "NONE_VERIFIED",
            "retryable": False,
        }
    )
    assert not_applied.side_effect_state == SIDE_EFFECT_NOT_APPLIED
    assert not_applied.raw_output["legacy_state_normalization"]["side_effect_state"] == "NONE_VERIFIED"


def test_f06_after_submit_failure_is_side_effect_unknown(tmp_path: Path) -> None:
    repository, queue_dir, _, _, attempt, _, request = _file_attempt(tmp_path, attempt_id="ATTEMPT-F06")
    event = ShadowBotQueueWatchdog(queue_dir, repository=repository)._write_recovery_result(  # noqa: SLF001
        request,
        {"request_file_sha256": attempt.request_file_sha256, "side_effect_state": "SUBMIT_CLICKED"},
        phase="SUBMIT_CLICKED",
    )
    data = json.loads(Path(event["result_path"]).read_text(encoding="utf-8"))
    assert data["status"] == STATUS_SIDE_EFFECT_UNKNOWN
    assert data["retryable"] is False


def test_f07_result_written_before_import_is_idempotent(tmp_path: Path) -> None:
    repository, queue_dir, runner, _, attempt, request_path, request = _file_attempt(
        tmp_path, attempt_id="ATTEMPT-F07"
    )
    result_path = queue_dir / "results" / "ATTEMPT-F07.result.json"
    _write_json_with_checksum(result_path, _result_payload(attempt, request))
    importer = ShadowBotResultImporter(repository, runner, queue_dir)
    first = importer.import_one(result_path)
    archive = Path(first["archive_dir"])
    for name, destination in (
        ("ATTEMPT-F07.ready.json", queue_dir / "inbox" / "ATTEMPT-F07.ready.json"),
        ("ATTEMPT-F07.ready.json.sha256", queue_dir / "inbox" / "ATTEMPT-F07.ready.json.sha256"),
        ("ATTEMPT-F07.result.json", result_path),
        ("ATTEMPT-F07.result.json.sha256", result_path.with_suffix(result_path.suffix + ".sha256")),
    ):
        destination.write_bytes((archive / name).read_bytes())
    second = importer.import_one(result_path)
    assert first["status"] == "IMPORTED"
    assert second["status"] == "ALREADY_IMPORTED"
    assert repository.get_shadowbot_operation("OP-RECOVERY").status == STATUS_VERIFIED


def test_f07_same_result_id_with_different_checksum_is_quarantined(tmp_path: Path) -> None:
    repository, queue_dir, runner, _, attempt, _, request = _file_attempt(
        tmp_path, attempt_id="ATTEMPT-F07-CONFLICT"
    )
    result_path = queue_dir / "results" / "ATTEMPT-F07-CONFLICT.result.json"
    original = _result_payload(attempt, request)
    _write_json_with_checksum(result_path, original)
    importer = ShadowBotResultImporter(repository, runner, queue_dir)
    first = importer.import_one(result_path)
    archive = Path(first["archive_dir"])
    for name, destination in (
        ("ATTEMPT-F07-CONFLICT.ready.json", queue_dir / "inbox" / "ATTEMPT-F07-CONFLICT.ready.json"),
        (
            "ATTEMPT-F07-CONFLICT.ready.json.sha256",
            queue_dir / "inbox" / "ATTEMPT-F07-CONFLICT.ready.json.sha256",
        ),
    ):
        destination.write_bytes((archive / name).read_bytes())
    conflict = dict(original)
    conflict["worker_id"] = "different-worker-same-result-id"
    _write_json_with_checksum(result_path, conflict)
    events = importer.import_available()
    assert events[0]["status"] == "QUARANTINED"
    assert repository.get_shadowbot_operation("OP-RECOVERY").status == OperationStatus.MANUAL_REVIEW.value


def test_f08_memory_success_without_result_becomes_unknown_after_lease_expiry(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    executor = ShadowBotExecutor(repository, RecordingRunner())
    executor.start_execution(_start_request(attempt_id="ATTEMPT-F08"))
    attempt = repository.get_shadowbot_execution_attempt("ATTEMPT-F08")
    expires = datetime.fromisoformat(str(_lease(attempt)["expires_at"]))
    events = ShadowBotLeaseWatchdog(repository).inspect(now=expires + timedelta(seconds=1))
    assert events[0]["error_code"] == "LEASE_EXPIRED"
    assert repository.get_shadowbot_execution_attempt("ATTEMPT-F08").status == STATUS_SIDE_EFFECT_UNKNOWN


def test_f09_checksum_mismatch_quarantines_and_reconciles(tmp_path: Path) -> None:
    repository, queue_dir, runner, _, attempt, _, request = _file_attempt(tmp_path, attempt_id="ATTEMPT-F09")
    result_path = queue_dir / "results" / "ATTEMPT-F09.result.json"
    _write_json_with_checksum(result_path, _result_payload(attempt, request), bad_checksum=True)
    events = ShadowBotResultImporter(repository, runner, queue_dir).import_available()
    assert events[0]["status"] == "QUARANTINED"
    assert repository.get_shadowbot_operation("OP-RECOVERY").status == OperationStatus.NEEDS_RECONCILIATION.value


def test_f10_expired_old_lease_cannot_write_back(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    ShadowBotExecutor(repository, RecordingRunner()).start_execution(_start_request(attempt_id="ATTEMPT-F10"))
    attempt = repository.get_shadowbot_execution_attempt("ATTEMPT-F10")
    lease = _lease(attempt)
    expires = datetime.fromisoformat(str(lease["expires_at"]))
    assert repository.expire_shadowbot_lease(attempt.execution_attempt_id, now=expires + timedelta(seconds=1))
    assert not repository.validate_shadowbot_lease(
        attempt.execution_attempt_id,
        owner_token=str(lease["owner_token"]),
        lease_version=int(lease["version"]),
        now=expires + timedelta(seconds=1),
    )
    with pytest.raises(ValidationError, match="LEASE_LOST"):
        ShadowBotExecutor(repository, RecordingRunner()).record_side_effect_checkpoint(
            operation_id=attempt.operation_id,
            execution_attempt_id=attempt.execution_attempt_id,
            side_effect_state="SUBMIT_INTENT_RECORDED",
            lease_owner_token=str(lease["owner_token"]),
            lease_version=int(lease["version"]),
        )


def test_f10_expired_old_worker_verified_result_is_late_evidence(tmp_path: Path) -> None:
    repository, queue_dir, runner, _, attempt, _, request = _file_attempt(
        tmp_path, attempt_id="ATTEMPT-F10-LATE-RESULT"
    )
    lease = _lease(attempt)
    expires = datetime.fromisoformat(str(lease["expires_at"]))
    assert repository.expire_shadowbot_lease(attempt.execution_attempt_id, now=expires + timedelta(seconds=1))
    result_path = queue_dir / "results" / "ATTEMPT-F10-LATE-RESULT.result.json"
    _write_json_with_checksum(result_path, _result_payload(attempt, request))
    events = ShadowBotResultImporter(repository, runner, queue_dir).import_available()
    assert events[0]["status"] == "QUARANTINED"
    frozen = repository.get_shadowbot_execution_attempt(attempt.execution_attempt_id)
    assert frozen.status == STATUS_SIDE_EFFECT_UNKNOWN
    assert not frozen.raw_output.get("result_id")
    assert repository.get_shadowbot_operation("OP-RECOVERY").status == OperationStatus.MANUAL_REVIEW.value


def test_lease_renewal_requires_current_owner_and_version(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    executor = ShadowBotExecutor(repository, RecordingRunner())
    executor.start_execution(_start_request(attempt_id="ATTEMPT-LEASE-RENEW"))
    attempt = repository.get_shadowbot_execution_attempt("ATTEMPT-LEASE-RENEW")
    lease = _lease(attempt)
    assert not executor.renew_lease(
        execution_attempt_id=attempt.execution_attempt_id,
        owner_token="stale-owner",
        lease_version=int(lease["version"]),
    )
    assert executor.renew_lease(
        execution_attempt_id=attempt.execution_attempt_id,
        owner_token=str(lease["owner_token"]),
        lease_version=int(lease["version"]),
        lease_seconds=1800,
    )


def test_result_missing_lease_identity_is_rejected(tmp_path: Path) -> None:
    repository, _, _, executor, attempt, _, request = _file_attempt(
        tmp_path, attempt_id="ATTEMPT-LEASE-MISSING"
    )
    data = _result_payload(attempt, request)
    data.pop("lease_owner_token")
    data.pop("lease_version")
    with pytest.raises(ValidationError, match="lease owner token/version are required"):
        executor.record_result(shadowbot_result_contract_from_data(data))
    assert repository.get_shadowbot_execution_attempt(attempt.execution_attempt_id).status == STATUS_RUNNING


def test_result_stale_lease_owner_is_rejected(tmp_path: Path) -> None:
    repository, _, _, executor, attempt, _, request = _file_attempt(
        tmp_path, attempt_id="ATTEMPT-LEASE-STALE"
    )
    data = _result_payload(attempt, request)
    data["lease_owner_token"] = "stale-owner"
    with pytest.raises(ValidationError, match="LEASE_LOST_WRITEBACK_REJECTED"):
        executor.record_result(shadowbot_result_contract_from_data(data))
    assert repository.get_shadowbot_execution_attempt(attempt.execution_attempt_id).status == STATUS_RUNNING


def test_result_wrong_lease_version_is_rejected(tmp_path: Path) -> None:
    repository, _, _, executor, attempt, _, request = _file_attempt(
        tmp_path, attempt_id="ATTEMPT-LEASE-WRONG-VERSION"
    )
    data = _result_payload(attempt, request)
    data["lease_version"] = int(data["lease_version"]) + 1
    with pytest.raises(ValidationError, match="LEASE_LOST_WRITEBACK_REJECTED"):
        executor.record_result(shadowbot_result_contract_from_data(data))
    assert repository.get_shadowbot_execution_attempt(attempt.execution_attempt_id).status == STATUS_RUNNING


def test_result_current_lease_identity_is_accepted(tmp_path: Path) -> None:
    repository, _, _, executor, attempt, _, request = _file_attempt(
        tmp_path, attempt_id="ATTEMPT-LEASE-CURRENT"
    )
    executor.record_result(shadowbot_result_contract_from_data(_result_payload(attempt, request)))
    assert repository.get_shadowbot_execution_attempt(attempt.execution_attempt_id).status == STATUS_VERIFIED


def test_f11_read_only_recovery_can_be_retryable_without_commit_semantics(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    watchdog = ShadowBotQueueWatchdog(tmp_path / "queue", repository=repository)
    request = {"execution_attempt_id": "READ-F11", "execution_mode": EXECUTION_MODE_READ_ONLY}
    event = watchdog._write_recovery_result(  # noqa: SLF001
        request,
        {"request_file_sha256": "hash", "side_effect_state": SIDE_EFFECT_NOT_STARTED},
        phase="UI_STARTED",
    )
    data = json.loads(Path(event["result_path"]).read_text(encoding="utf-8"))
    assert data["status"] == STATUS_FAILED
    assert data["retryable"] is True


def test_f12_duplicate_ready_is_quarantined(tmp_path: Path) -> None:
    repository, queue_dir, _, _, _, _, request = _file_attempt(tmp_path, attempt_id="ATTEMPT-F12")
    duplicate = queue_dir / "inbox" / "duplicate.ready.json"
    _write_json_with_checksum(duplicate, request)
    events = ShadowBotQueueWatchdog(queue_dir, repository=repository).inspect()
    assert any(event["error_code"] == "DUPLICATE_READY_REQUEST" for event in events)


def test_i01_verified_operation_refuses_stale_commit(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    payload = _payload()
    repository.insert_shadowbot_operation(
        ShadowBotOperationLedger(
            operation_id=payload.operation_id,
            task_id=payload.task_id,
            platform=payload.platform,
            product_identity=payload.product_identity,
            expected_old_price=payload.expected_old_price,
            target_price=payload.target_price,
            status=STATUS_VERIFIED,
            approved_payload_hash=compute_approved_payload_hash(payload),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    runner = RecordingRunner()
    result = ShadowBotExecutor(repository, runner).start_execution(_start_request(attempt_id="ATTEMPT-I01"))
    assert result.status == "ALREADY_APPLIED"
    assert runner.calls == []


def test_i02_reconciliation_ready_is_frozen(tmp_path: Path) -> None:
    repository, queue_dir, _, _, _, request_path, _ = _file_attempt(tmp_path, attempt_id="ATTEMPT-I02")
    repository.update_shadowbot_execution_attempt("ATTEMPT-I02", status=STATUS_START_UNKNOWN, ended_at=datetime.now(UTC))
    repository.update_shadowbot_operation_status("OP-RECOVERY", OperationStatus.NEEDS_RECONCILIATION.value, lock_owner="")
    events = ShadowBotQueueWatchdog(queue_dir, repository=repository).inspect()
    assert any(event["error_code"] == "FROZEN_READY_REQUEST" for event in events)
    assert not request_path.exists()


def test_i03_orphan_ready_is_quarantined(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    queue_dir = tmp_path / "queue"
    ready = queue_dir / "inbox" / "orphan.ready.json"
    _write_json_with_checksum(ready, {"execution_attempt_id": "ORPHAN-I03"})
    events = ShadowBotQueueWatchdog(queue_dir, repository=repository).inspect()
    assert events[0]["error_code"] == "ORPHAN_READY_REQUEST"


def test_i04_binding_mismatch_is_quarantined(tmp_path: Path) -> None:
    repository, queue_dir, runner, _, attempt, _, request = _file_attempt(tmp_path, attempt_id="ATTEMPT-I04")
    result = _result_payload(attempt, request)
    result["instruction_hash"] = "wrong"
    result_path = queue_dir / "results" / "ATTEMPT-I04.result.json"
    _write_json_with_checksum(result_path, result)
    events = ShadowBotResultImporter(repository, runner, queue_dir).import_available()
    assert events[0]["status"] == "QUARANTINED"


def test_i05_complete_verified_result_advances_database(tmp_path: Path) -> None:
    repository, queue_dir, runner, _, attempt, _, request = _file_attempt(tmp_path, attempt_id="ATTEMPT-I05")
    result_path = queue_dir / "results" / "ATTEMPT-I05.result.json"
    _write_json_with_checksum(result_path, _result_payload(attempt, request))
    ShadowBotResultImporter(repository, runner, queue_dir).import_one(result_path)
    assert repository.get_shadowbot_execution_attempt("ATTEMPT-I05").status == STATUS_VERIFIED
    assert repository.get_shadowbot_operation("OP-RECOVERY").status == STATUS_VERIFIED


def test_i06_conflicting_result_after_verified_enters_manual_review(tmp_path: Path) -> None:
    repository, queue_dir, runner, _, attempt, _, request = _file_attempt(tmp_path, attempt_id="ATTEMPT-I06")
    importer = ShadowBotResultImporter(repository, runner, queue_dir)
    result_path = queue_dir / "results" / "ATTEMPT-I06.result.json"
    verified = _result_payload(attempt, request)
    _write_json_with_checksum(result_path, verified)
    first = importer.import_one(result_path)
    archive = Path(first["archive_dir"])
    (queue_dir / "inbox" / "ATTEMPT-I06.ready.json").write_bytes((archive / "ATTEMPT-I06.ready.json").read_bytes())
    (queue_dir / "inbox" / "ATTEMPT-I06.ready.json.sha256").write_bytes(
        (archive / "ATTEMPT-I06.ready.json.sha256").read_bytes()
    )
    conflict = _result_payload(attempt, request, status=STATUS_FAILED)
    conflict["result_id"] = "RESULT-CONFLICT-I06"
    _write_json_with_checksum(result_path, conflict)
    events = importer.import_available()
    assert events[0]["status"] == "QUARANTINED"
    assert repository.get_shadowbot_operation("OP-RECOVERY").status == OperationStatus.MANUAL_REVIEW.value


def test_i07_duplicate_active_commit_attempts_are_frozen(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    executor = ShadowBotExecutor(repository, RecordingRunner())
    executor.start_execution(_start_request(attempt_id="ATTEMPT-I07-A"))
    first = repository.get_shadowbot_execution_attempt("ATTEMPT-I07-A")
    repository.insert_shadowbot_execution_attempt(
        ShadowBotExecutionAttempt(
            execution_attempt_id="ATTEMPT-I07-B",
            operation_id=first.operation_id,
            execution_mode=EXECUTION_MODE_COMMIT,
            shadowbot_run_id="RUN-I07-B",
            status=STATUS_RUNNING,
            side_effect_state=SIDE_EFFECT_NOT_STARTED,
            started_at=datetime.now(UTC),
            instruction_hash="instruction-i07-b",
            raw_output={"lease": {"owner_token": "other", "version": 2, "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(), "active": True}},
        )
    )
    events = ShadowBotLeaseWatchdog(repository).inspect()
    assert events[0]["error_code"] == "DUPLICATE_ACTIVE_COMMIT_ATTEMPT"
    assert repository.get_shadowbot_operation("OP-RECOVERY").status == OperationStatus.MANUAL_REVIEW.value
    frozen_attempts = repository.list_shadowbot_execution_attempts(operation_id="OP-RECOVERY")
    assert {attempt.status for attempt in frozen_attempts} == {STATUS_SIDE_EFFECT_UNKNOWN}
    assert all(attempt.ended_at is not None for attempt in frozen_attempts)
    assert ShadowBotLeaseWatchdog(repository).inspect() == []


def test_i07_manual_evidence_can_authorize_and_create_fresh_attempt_after_freeze(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    runner = RecordingRunner()
    ShadowBotExecutor(repository, runner).start_execution(_start_request(attempt_id="ATTEMPT-I07-CLOSE-A"))
    first = repository.get_shadowbot_execution_attempt("ATTEMPT-I07-CLOSE-A")
    approval = _approval()
    repository.insert_shadowbot_execution_attempt(
        ShadowBotExecutionAttempt(
            execution_attempt_id="ATTEMPT-I07-CLOSE-B",
            operation_id=first.operation_id,
            execution_mode=EXECUTION_MODE_COMMIT,
            shadowbot_run_id="RUN-I07-CLOSE-B",
            status=STATUS_RUNNING,
            side_effect_state=SIDE_EFFECT_NOT_STARTED,
            started_at=datetime.now(UTC),
            instruction_hash="instruction-i07-close-b",
            request_file_sha256="request-i07-close-b",
            raw_output={
                "approved_payload_hash": approval.approved_payload_hash,
                "approval_id": approval.approval_id,
                "approval_expires_at": approval.expires_at.isoformat(),
                "lease": {
                    "owner_token": "other-owner",
                    "version": 2,
                    "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                    "active": True,
                },
            },
        )
    )
    ShadowBotLeaseWatchdog(repository).inspect()
    source = repository.get_shadowbot_execution_attempt("ATTEMPT-I07-CLOSE-B")
    evidence = RetryEvidence(
        evidence_type=EVIDENCE_NOT_APPLIED_RESULT,
        result_id="MANUAL-EVIDENCE-I07-CLOSE",
        instruction_hash=source.instruction_hash,
        request_file_sha256=source.request_file_sha256,
        approved_payload_hash=approval.approved_payload_hash,
        side_effect_state=SIDE_EFFECT_NOT_APPLIED,
        checksum_valid=True,
        failed_phase="MANUAL_PLATFORM_RECONCILIATION",
    )
    authorization = RetryPolicyService(repository).issue_manual(
        source_execution_attempt_id=source.execution_attempt_id,
        actor="operator-i07",
        reason="platform evidence confirms target change was not applied",
        evidence=evidence,
    )
    result = ShadowBotExecutor(repository, runner).start_execution(
        _start_request(
            attempt_id="ATTEMPT-I07-CLOSE-RETRY",
            retry_authorization_id=authorization.retry_authorization_id,
        )
    )
    fresh = repository.get_shadowbot_execution_attempt(result.execution_attempt_id)
    assert fresh.status == STATUS_RUNNING
    assert int(_lease(fresh)["version"]) > int(_lease(source)["version"])
    assert repository.get_retry_authorization(authorization.retry_authorization_id).status == "CONSUMED"


def test_i08_running_without_queue_moves_to_reconciliation_on_expiry(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    ShadowBotExecutor(repository, RecordingRunner()).start_execution(_start_request(attempt_id="ATTEMPT-I08"))
    attempt = repository.get_shadowbot_execution_attempt("ATTEMPT-I08")
    expiry = datetime.fromisoformat(str(_lease(attempt)["expires_at"]))
    ShadowBotLeaseWatchdog(repository).inspect(now=expiry + timedelta(seconds=1))
    assert repository.get_shadowbot_operation("OP-RECOVERY").status == OperationStatus.NEEDS_RECONCILIATION.value


def test_i09_stale_owner_token_is_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    ShadowBotExecutor(repository, RecordingRunner()).start_execution(_start_request(attempt_id="ATTEMPT-I09"))
    attempt = repository.get_shadowbot_execution_attempt("ATTEMPT-I09")
    assert not repository.validate_shadowbot_lease(
        attempt.execution_attempt_id,
        owner_token="stale-owner",
        lease_version=int(_lease(attempt)["version"]),
        now=datetime.now(UTC),
    )


def test_i10_orphan_result_is_quarantined_without_database_creation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    queue_dir = tmp_path / "queue"
    result_path = queue_dir / "results" / "orphan.result.json"
    _write_json_with_checksum(
        result_path,
        {
            "execution_attempt_id": "ORPHAN-I10",
            "status": STATUS_START_UNKNOWN,
            "run_success_flag": None,
            "business_operation_completed": None,
            "side_effect_state": SIDE_EFFECT_UNKNOWN,
            "retryable": False,
        },
    )
    events = ShadowBotResultImporter(repository, RecordingRunner(), queue_dir).import_available()
    assert events[0]["status"] == "QUARANTINED"
    assert repository.get_shadowbot_execution_attempt("ORPHAN-I10") is None

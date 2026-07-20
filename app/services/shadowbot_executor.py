from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from app.enums import TaskStatus
from app.enums import ReviewTaskStatus
from app.exceptions import ValidationError
from app.models import ExecutionLog, ReviewTask, ShadowBotExecutionAttempt, ShadowBotOperationLedger
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.notification_outbox import OutboxReviewNotificationService
from app.services.runtime import RuntimeTaskService
from app.services.shadowbot_state import (
    AttemptStatus,
    OperationStatus,
    ResultStatus,
    SideEffectState,
    attempt_status_from_result,
    normalize_result_status,
    normalize_side_effect_state,
    operation_status_from_result,
    validate_result_state,
)
from app.services.shadowbot_product_read import (
    build_read_batch_id,
    canonical_request_digest,
    compute_multi_product_instruction_hash,
    normalize_multi_product_request,
)
from app.services.shadowbot_price_batch import sha256_jcs
from app.services.shadowbot_product_read import normalize_grade, normalize_sku, normalize_text
from app.utils import serialize_decimal, utc_now


SHADOWBOT_EXECUTOR_NAME = "shadowbot_executor"

EXECUTION_MODE_READ_ONLY = "READ_ONLY"
EXECUTION_MODE_FILL_PREVIEW = "FILL_PREVIEW"
EXECUTION_MODE_COMMIT = "COMMIT"
EXECUTION_MODE_RECONCILE = "RECONCILE"

STATUS_PENDING = OperationStatus.PENDING.value
STATUS_STARTING = AttemptStatus.STARTING.value
STATUS_RUNNING = AttemptStatus.RUNNING.value
STATUS_SUCCESS = "SUCCESS"
STATUS_ALREADY_APPLIED = "ALREADY_APPLIED"
STATUS_READ_COMPLETED = ResultStatus.READ_COMPLETED.value
STATUS_PREVIEW_COMPLETED = ResultStatus.PREVIEW_COMPLETED.value
STATUS_VERIFIED = ResultStatus.VERIFIED.value
STATUS_NOT_APPLIED = ResultStatus.NOT_APPLIED.value
STATUS_FAILED = ResultStatus.FAILED.value
STATUS_START_FAILED = ResultStatus.START_FAILED.value
STATUS_START_UNKNOWN = ResultStatus.START_UNKNOWN.value
STATUS_SIDE_EFFECT_UNKNOWN = ResultStatus.SIDE_EFFECT_UNKNOWN.value
STATUS_RETRY_AUTHORIZED = OperationStatus.RETRY_AUTHORIZED.value
STATUS_NEEDS_RECONCILIATION = OperationStatus.NEEDS_RECONCILIATION.value
STATUS_MANUAL_REVIEW = OperationStatus.MANUAL_REVIEW.value

SIDE_EFFECT_NOT_STARTED = SideEffectState.NOT_STARTED.value
SIDE_EFFECT_SUBMIT_INTENT_RECORDED = SideEffectState.SUBMIT_INTENT_RECORDED.value
SIDE_EFFECT_SUBMIT_CLICKED = SideEffectState.SUBMIT_CLICKED.value
SIDE_EFFECT_VERIFIED = SideEffectState.VERIFIED.value
SIDE_EFFECT_UNKNOWN = SideEffectState.UNKNOWN.value
SIDE_EFFECT_NOT_APPLIED = SideEffectState.NOT_APPLIED.value
LOGIN_VERIFICATION_REVIEW_TYPE = "shadowbot_login_verification"

SIDE_EFFECT_RECONCILE_REQUIRED = {
    SIDE_EFFECT_SUBMIT_INTENT_RECORDED,
    SIDE_EFFECT_SUBMIT_CLICKED,
    SIDE_EFFECT_UNKNOWN,
}

ALLOWED_EXECUTION_MODES = {
    EXECUTION_MODE_READ_ONLY,
    EXECUTION_MODE_FILL_PREVIEW,
    EXECUTION_MODE_COMMIT,
    EXECUTION_MODE_RECONCILE,
}
ALLOWED_RESULT_STATUSES = {
    STATUS_READ_COMPLETED,
    STATUS_PREVIEW_COMPLETED,
    STATUS_VERIFIED,
    STATUS_NOT_APPLIED,
    STATUS_FAILED,
    STATUS_START_FAILED,
    STATUS_START_UNKNOWN,
    STATUS_SIDE_EFFECT_UNKNOWN,
}
ALLOWED_SIDE_EFFECT_STATES = {
    SIDE_EFFECT_NOT_STARTED,
    SIDE_EFFECT_SUBMIT_INTENT_RECORDED,
    SIDE_EFFECT_SUBMIT_CLICKED,
    SIDE_EFFECT_VERIFIED,
    SIDE_EFFECT_UNKNOWN,
    SIDE_EFFECT_NOT_APPLIED,
}


@dataclass(slots=True)
class ShadowBotApprovedPayload:
    operation_id: str
    task_id: str
    platform: str
    product_identity: dict[str, Any]
    expected_old_price: Decimal
    target_price: Decimal


@dataclass(slots=True)
class ShadowBotApproval:
    approval_id: str
    approval_status: str
    approved_payload: ShadowBotApprovedPayload
    approved_payload_hash: str
    approved_at: datetime
    expires_at: datetime | None = None
    approval_contract_version: int = 1
    approved_payload_view: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ShadowBotExecutionRequest:
    operation_id: str
    execution_attempt_id: str
    execution_mode: str
    approval: ShadowBotApproval
    lock_owner: str = SHADOWBOT_EXECUTOR_NAME
    retry_authorization_id: str = ""
    lease_seconds: int = 900
    runner_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ShadowBotStartResult:
    shadowbot_run_id: str
    raw_output: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ShadowBotExecutorStartResult:
    operation_id: str
    execution_attempt_id: str
    shadowbot_run_id: str
    status: str
    side_effect_state: str
    next_execution_mode: str | None = None


@dataclass(slots=True)
class ShadowBotResultContract:
    execution_attempt_id: str
    status: str
    run_success_flag: bool | None
    business_operation_completed: bool | None
    side_effect_state: str
    retryable: bool
    error_code: str = ""
    operation_id: str = ""
    task_id: str = ""
    execution_mode: str = ""
    instruction_hash: str = ""
    request_file_sha256: str = ""
    result_id: str = ""
    lease_owner_token: str = ""
    lease_version: int = 0
    worker_id: str = ""
    raw_output: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ShadowBotTimeoutClassification:
    operation_id: str
    status: str
    side_effect_state: str
    retryable: bool
    next_execution_mode: str


class ShadowBotStartBoundaryError(Exception):
    """Runner failure with an explicit, auditable publication boundary."""

    def __init__(self, message: str, *, published: bool, raw_output: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.published = published
        self.raw_output = dict(raw_output or {})


class ShadowBotTaskRunner(Protocol):
    def start(self, payload: dict[str, Any]) -> ShadowBotStartResult:
        ...


JsonHttpRequester = Callable[[Request, float], dict[str, Any]]


class ShadowBotFileQueueRunner:
    def __init__(self, queue_dir: Path, *, command: str = "") -> None:
        self.queue_dir = queue_dir
        self.command = command

    @classmethod
    def from_environment(cls) -> "ShadowBotFileQueueRunner":
        queue_dir = Path(
            os.environ.get("SHADOWBOT_QUEUE_DIR")
            or os.environ.get("SHADOWBOT_REQUEST_DIR")
            or "data/runtime/shadowbot_queue"
        )
        return cls(queue_dir, command=os.environ.get("SHADOWBOT_RUNNER_COMMAND", ""))

    def start(self, payload: dict[str, Any]) -> ShadowBotStartResult:
        is_multi_product = payload.get("contract_version") == 2
        if is_multi_product:
            _validate_multi_product_queue_request(payload)
        else:
            _validate_queue_request(payload)
        execution_attempt_id = str(payload["execution_attempt_id"])
        instruction_hash = (
            compute_multi_product_instruction_hash(payload)
            if is_multi_product
            else compute_instruction_hash(payload)
        )
        supplied_instruction_hash = str(payload.get("instruction_hash") or "")
        if supplied_instruction_hash and supplied_instruction_hash != instruction_hash:
            raise ValidationError("instruction_hash does not match the execution instruction.")
        request_payload = dict(payload)
        request_payload["instruction_hash"] = instruction_hash

        inbox_dir = self.queue_dir / "inbox"
        for name in ("inbox", "working", "results", "archive", "quarantine", "evidence", "control"):
            (self.queue_dir / name).mkdir(parents=True, exist_ok=True)
        request_path = inbox_dir / f"{execution_attempt_id}.ready.json"
        checksum_path = request_path.with_suffix(request_path.suffix + ".sha256")
        if request_path.exists() or checksum_path.exists():
            raise ValidationError("execution_attempt_id already exists in the ShadowBot queue.")
        request_bytes = _canonical_file_json(request_payload)
        request_file_sha256 = hashlib.sha256(request_bytes).hexdigest()
        try:
            _atomic_publish(checksum_path, (request_file_sha256 + "\n").encode("ascii"))
            _atomic_publish(request_path, request_bytes)
        except OSError as exc:
            published = request_path.exists()
            if not published and checksum_path.exists():
                checksum_path.unlink(missing_ok=True)
            raise ShadowBotStartBoundaryError(
                "ShadowBot file queue publication failed.",
                published=published,
                raw_output={
                    "queue_request_path": str(request_path),
                    "request_file_sha256": request_file_sha256,
                },
            ) from exc
        if self.command:
            try:
                subprocess.Popen(  # noqa: S603
                    [self.command, str(request_path)],
                    cwd=str(Path.cwd()),
                    close_fds=True,
                )
            except OSError as exc:
                raise ShadowBotStartBoundaryError(
                    "ShadowBot command launch failed after queue publication.",
                    published=True,
                    raw_output={
                        "queue_request_path": str(request_path),
                        "request_file_sha256": request_file_sha256,
                    },
                ) from exc
        return ShadowBotStartResult(
            shadowbot_run_id=f"filequeue:{execution_attempt_id}",
            raw_output={
                "queue_request_path": str(request_path),
                "request_file_sha256": request_file_sha256,
                "instruction_hash": instruction_hash,
                "runner": "file_queue",
                "command_started": bool(self.command),
            },
        )

    def archive_attempt_artifacts(self, execution_attempt_id: str) -> None:
        """Remove an old attempt from executable queue locations before retry."""
        archive_dir = self.queue_dir / "archive" / execution_attempt_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        candidates = (
            self.queue_dir / "inbox" / f"{execution_attempt_id}.ready.json",
            self.queue_dir / "inbox" / f"{execution_attempt_id}.ready.json.sha256",
            self.queue_dir / "working" / f"{execution_attempt_id}.request.json",
            self.queue_dir / "working" / f"{execution_attempt_id}.request.json.sha256",
            self.queue_dir / "working" / f"{execution_attempt_id}.phase.json",
            self.queue_dir / "results" / f"{execution_attempt_id}.result.json",
            self.queue_dir / "results" / f"{execution_attempt_id}.result.json.sha256",
        )
        for source in candidates:
            if not source.exists():
                continue
            destination = archive_dir / source.name
            if destination.exists():
                raise ValidationError("OLD_QUEUE_ARTIFACT_CONFLICT")
            os.replace(source, destination)


class FileDropShadowBotTaskRunner(ShadowBotFileQueueRunner):
    """Compatibility name for the file queue runner."""

    def __init__(
        self,
        request_dir: Path | None = None,
        *,
        queue_dir: Path | None = None,
        command: str = "",
    ) -> None:
        super().__init__(queue_dir or request_dir or Path("data/runtime/shadowbot_queue"), command=command)


class YingdaoOpenApiJobRunner:
    """Start a ShadowBot app through Yingdao OpenAPI JOB运行/启动应用."""

    def __init__(
        self,
        *,
        base_url: str,
        access_key_id: str,
        access_key_secret: str,
        robot_uuid: str,
        account_name: str = "",
        robot_client_group_uuid: str = "",
        wait_timeout_seconds: int = 600,
        run_timeout_seconds: int = 600,
        priority: str = "middle",
        request_param_name: str = "request_json",
        include_flat_params: bool = True,
        http_requester: JsonHttpRequester | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.robot_uuid = robot_uuid
        self.account_name = account_name
        self.robot_client_group_uuid = robot_client_group_uuid
        self.wait_timeout_seconds = wait_timeout_seconds
        self.run_timeout_seconds = run_timeout_seconds
        self.priority = priority
        self.request_param_name = request_param_name
        self.include_flat_params = include_flat_params
        self.http_requester = http_requester or _request_json

    @classmethod
    def from_environment(cls) -> "YingdaoOpenApiJobRunner":
        return cls(
            base_url=os.environ.get("YINGDAO_API_BASE_URL", "https://api.yingdao.com"),
            access_key_id=os.environ.get("YINGDAO_ACCESS_KEY_ID", ""),
            access_key_secret=os.environ.get("YINGDAO_ACCESS_KEY_SECRET", ""),
            robot_uuid=os.environ.get("YINGDAO_ROBOT_UUID", ""),
            account_name=os.environ.get("YINGDAO_ACCOUNT_NAME", ""),
            robot_client_group_uuid=os.environ.get("YINGDAO_ROBOT_CLIENT_GROUP_UUID", ""),
            wait_timeout_seconds=_int_from_env("YINGDAO_WAIT_TIMEOUT_SECONDS", 600),
            run_timeout_seconds=_int_from_env("YINGDAO_RUN_TIMEOUT_SECONDS", 600),
            priority=os.environ.get("YINGDAO_PRIORITY", "middle"),
            request_param_name=os.environ.get("YINGDAO_REQUEST_PARAM_NAME", "request_json"),
            include_flat_params=os.environ.get("YINGDAO_INCLUDE_FLAT_PARAMS", "1").strip().lower()
            not in {"0", "false", "no"},
        )

    def start(self, payload: dict[str, Any]) -> ShadowBotStartResult:
        try:
            self._validate_config()
        except ValidationError as exc:
            raise ShadowBotStartBoundaryError(str(exc), published=False) from exc
        access_token = self._create_access_token()
        request_body = self._build_start_job_body(payload)
        response = self._post_json("/oapi/dispatch/v2/job/start", request_body, access_token)
        data = _require_response_data(response, endpoint="job/start")
        job_uuid = str(data.get("jobUuid") or "")
        if not job_uuid:
            raise ValidationError("Yingdao job/start response did not include jobUuid.")
        return ShadowBotStartResult(
            shadowbot_run_id=f"yingdao-job:{job_uuid}",
            raw_output={
                "runner": "yingdao_openapi_job",
                "jobUuid": job_uuid,
                "idempotentFlag": bool(data.get("idempotentFlag", False)),
                "robotUuid": self.robot_uuid,
                "request": _redact_yingdao_request_body(request_body),
                "response": response,
            },
        )

    def query_job(self, job_uuid: str) -> dict[str, Any]:
        if not job_uuid:
            raise ValidationError("jobUuid is required.")
        self._validate_config()
        access_token = self._create_access_token()
        return self._post_json("/oapi/dispatch/v2/job/query", {"jobUuid": job_uuid}, access_token)

    def query_robot_params(self) -> dict[str, Any]:
        self._validate_config()
        access_token = self._create_access_token()
        query = urlencode({"robotUuid": self.robot_uuid})
        request = Request(
            f"{self.base_url}/oapi/robot/v2/queryRobotParam?{query}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            method="GET",
        )
        return self.http_requester(request, 30.0)

    def _validate_config(self) -> None:
        if not self.access_key_id:
            raise ValidationError("YINGDAO_ACCESS_KEY_ID is required for YingdaoOpenApiJobRunner.")
        if not self.access_key_secret:
            raise ValidationError("YINGDAO_ACCESS_KEY_SECRET is required for YingdaoOpenApiJobRunner.")
        if not self.robot_uuid:
            raise ValidationError("YINGDAO_ROBOT_UUID is required for YingdaoOpenApiJobRunner.")
        if not self.account_name and not self.robot_client_group_uuid:
            raise ValidationError(
                "YINGDAO_ACCOUNT_NAME or YINGDAO_ROBOT_CLIENT_GROUP_UUID is required for YingdaoOpenApiJobRunner."
            )

    def _create_access_token(self) -> str:
        query = urlencode({"accessKeyId": self.access_key_id, "accessKeySecret": self.access_key_secret})
        response = self.http_requester(Request(f"{self.base_url}/oapi/token/v2/token/create?{query}"), 30.0)
        data = _require_response_data(response, endpoint="token/create")
        access_token = str(data.get("accessToken") or "")
        if not access_token:
            raise ValidationError("Yingdao token/create response did not include accessToken.")
        return access_token

    def _post_json(self, path: str, body: dict[str, Any], access_token: str) -> dict[str, Any]:
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=encoded,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        return self.http_requester(request, 30.0)

    def _build_start_job_body(self, payload: dict[str, Any]) -> dict[str, Any]:
        execution_attempt_id = str(payload.get("execution_attempt_id") or "")
        body: dict[str, Any] = {
            "robotUuid": self.robot_uuid,
            "idempotentUuid": _to_yingdao_idempotent_uuid(execution_attempt_id or uuid4().hex),
            "waitTimeoutSeconds": self.wait_timeout_seconds,
            "runTimeout": self.run_timeout_seconds,
            "priority": self.priority,
            "params": self._build_params(payload),
        }
        if self.robot_client_group_uuid:
            body["robotClientGroupUuid"] = self.robot_client_group_uuid
            body["executeScope"] = "any"
        else:
            body["accountName"] = self.account_name
        return body

    def _build_params(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        params = [
            {
                "name": self.request_param_name,
                "value": json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                "type": "str",
            }
        ]
        if self.include_flat_params:
            for key in (
                "operation_id",
                "task_id",
                "execution_attempt_id",
                "execution_mode",
                "platform",
                "expected_old_price",
                "target_price",
            ):
                value = payload.get(key)
                if value is not None:
                    params.append({"name": key, "value": str(value), "type": "str"})
            product_identity = payload.get("product_identity")
            if isinstance(product_identity, dict):
                for key in ("sku", "internal_sku", "name", "grade", "spec"):
                    value = product_identity.get(key)
                    if value is not None:
                        params.append({"name": f"product_{key}", "value": str(value), "type": "str"})
        return params


def build_shadowbot_task_runner_from_environment() -> ShadowBotTaskRunner:
    runner_type = os.environ.get("SHADOWBOT_RUNNER_TYPE", "filedrop").strip().lower()
    if runner_type in {"filequeue", "file_queue", "filedrop", "file_drop", ""}:
        return ShadowBotFileQueueRunner.from_environment()
    if runner_type in {"yingdao_openapi", "yingdao_job", "openapi_job"}:
        return YingdaoOpenApiJobRunner.from_environment()
    raise ValidationError(f"unsupported SHADOWBOT_RUNNER_TYPE: {runner_type}")


class ShadowBotExecutor:
    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        runner: ShadowBotTaskRunner,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.runtime_task_service = RuntimeTaskService(repository)
        self.notification_outbox_service = OutboxReviewNotificationService(repository)

    def start_execution(self, request: ShadowBotExecutionRequest) -> ShadowBotExecutorStartResult:
        self.repository.init_schema()
        _validate_execution_mode(request.execution_mode)
        _reject_fault_injection(request.runner_payload)
        payload = self._validate_approval(request)
        self._ensure_operation(payload, request.approval.approved_payload_hash)
        existing_operation = self.repository.get_shadowbot_operation(payload.operation_id)
        if (
            request.execution_mode == EXECUTION_MODE_COMMIT
            and existing_operation is not None
            and existing_operation.status in {STATUS_SUCCESS, STATUS_ALREADY_APPLIED, STATUS_VERIFIED}
        ):
            return ShadowBotExecutorStartResult(
                operation_id=payload.operation_id,
                execution_attempt_id=request.execution_attempt_id,
                shadowbot_run_id="",
                status=STATUS_ALREADY_APPLIED,
                side_effect_state=SIDE_EFFECT_VERIFIED,
            )

        if request.execution_mode == EXECUTION_MODE_COMMIT and (
            self._requires_reconciliation(payload.operation_id)
            or (existing_operation is not None and existing_operation.status in {
                STATUS_NEEDS_RECONCILIATION,
                STATUS_MANUAL_REVIEW,
                "MANUAL_HANDLED",
            })
        ):
            return ShadowBotExecutorStartResult(
                operation_id=payload.operation_id,
                execution_attempt_id=request.execution_attempt_id,
                shadowbot_run_id="",
                status=STATUS_NEEDS_RECONCILIATION,
                side_effect_state=SIDE_EFFECT_UNKNOWN,
                next_execution_mode=EXECUTION_MODE_RECONCILE,
            )

        runner_payload = _build_queue_request_payload(
            operation_id=payload.operation_id,
            task_id=payload.task_id,
            execution_attempt_id=request.execution_attempt_id,
            execution_mode=request.execution_mode,
            platform_name=payload.platform,
            product_identity=payload.product_identity,
            expected_old_price=payload.expected_old_price,
            target_price=payload.target_price,
            approved_payload_hash=request.approval.approved_payload_hash,
            approval_id=request.approval.approval_id,
            approval_expires_at=request.approval.expires_at,
            overrides=request.runner_payload,
        )
        instruction_hash = str(runner_payload["instruction_hash"])
        now = utc_now()
        owner_token = (
            f"{request.lock_owner}:{uuid4().hex}"
            if request.retry_authorization_id
            else request.lock_owner
        )
        lease_expires_at = now + timedelta(seconds=max(int(request.lease_seconds), 1))
        attempt = ShadowBotExecutionAttempt(
            execution_attempt_id=request.execution_attempt_id,
            operation_id=payload.operation_id,
            execution_mode=request.execution_mode,
            shadowbot_run_id="",
            status=STATUS_STARTING,
            side_effect_state=SIDE_EFFECT_NOT_STARTED,
            started_at=now,
            instruction_hash=instruction_hash,
            raw_output={
                "approved_payload_hash": request.approval.approved_payload_hash,
                "approval_id": request.approval.approval_id,
                "approval_expires_at": (
                    request.approval.expires_at.isoformat() if request.approval.expires_at else ""
                ),
            },
        )
        if request.retry_authorization_id:
            if request.execution_mode != EXECUTION_MODE_COMMIT:
                raise ValidationError("RETRY_AUTHORIZATION_ONLY_VALID_FOR_COMMIT")
            authorization = self.repository.get_retry_authorization(request.retry_authorization_id)
            if (
                authorization is None
                or authorization.status != "ACTIVE"
                or authorization.operation_id != payload.operation_id
            ):
                raise ValidationError("RETRY_AUTHORIZATION_CONSUME_CONFLICT")
            archive_old = getattr(self.runner, "archive_attempt_artifacts", None)
            if callable(archive_old):
                archive_old(authorization.source_execution_attempt_id)
            claimed_attempt = self.repository.consume_retry_authorization_and_create_attempt(
                request.retry_authorization_id,
                attempt,
                owner_token=owner_token,
                lease_expires_at=lease_expires_at,
                approved_payload_hash=request.approval.approved_payload_hash,
                consumed_at=now,
            )
            if claimed_attempt is None:
                raise ValidationError("RETRY_AUTHORIZATION_CONSUME_CONFLICT")
        else:
            if (
                request.execution_mode == EXECUTION_MODE_COMMIT
                and existing_operation is not None
                and existing_operation.status in {
                    OperationStatus.FAILED.value,
                    OperationStatus.NOT_APPLIED.value,
                    OperationStatus.RETRY_AUTHORIZED.value,
                }
            ):
                raise ValidationError("RETRY_AUTHORIZATION_REQUIRED")
            claimed_attempt = self.repository.create_shadowbot_attempt_with_lease(
                attempt,
                owner_token=owner_token,
                lease_expires_at=lease_expires_at,
                expected_operation_statuses=(OperationStatus.PENDING.value,),
            )
            if claimed_attempt is None:
                raise ValidationError("ShadowBot operation is already locked or execution_attempt_id exists.")

        lease = claimed_attempt.raw_output.get("lease", {})
        lease_version = int(lease.get("version") or 0)
        runner_payload.update(
            {
                "lease_owner_token": owner_token,
                "lease_version": lease_version,
                "lease_expires_at": str(lease.get("expires_at") or ""),
            }
        )
        try:
            start_result = self.runner.start(runner_payload)
        except Exception as exc:
            boundary_known = isinstance(exc, ShadowBotStartBoundaryError)
            published = exc.published if boundary_known else None
            attempt_status = STATUS_START_FAILED if boundary_known and not published else STATUS_START_UNKNOWN
            operation_status = (
                STATUS_NEEDS_RECONCILIATION if attempt_status == STATUS_START_UNKNOWN else OperationStatus.FAILED.value
            )
            error_raw = {
                "error_code": "RUNNER_START_UNKNOWN" if attempt_status == STATUS_START_UNKNOWN else "RUNNER_START_FAILED",
                "error_type": type(exc).__name__,
                "published": published,
            }
            if isinstance(exc, ShadowBotStartBoundaryError):
                error_raw.update(exc.raw_output)
            marked = self.repository.mark_shadowbot_start_outcome(
                request.execution_attempt_id,
                owner_token=owner_token,
                lease_version=lease_version,
                attempt_status=attempt_status,
                operation_status=operation_status,
                instruction_hash=instruction_hash,
                raw_output=error_raw,
                ended_at=utc_now(),
            )
            if not marked:
                raise ValidationError("SHADOWBOT_LEASE_LOST_DURING_START") from exc
            raise
        if not start_result.shadowbot_run_id:
            self.repository.mark_shadowbot_start_outcome(
                request.execution_attempt_id,
                owner_token=owner_token,
                lease_version=lease_version,
                attempt_status=STATUS_START_UNKNOWN,
                operation_status=STATUS_NEEDS_RECONCILIATION,
                instruction_hash=instruction_hash,
                raw_output={"error_code": "RUNNER_START_ID_UNKNOWN"},
                ended_at=utc_now(),
            )
            raise ValidationError("ShadowBot runner did not return shadowbot_run_id.")

        marked = self.repository.mark_shadowbot_start_outcome(
            request.execution_attempt_id,
            owner_token=owner_token,
            lease_version=lease_version,
            attempt_status=STATUS_RUNNING,
            operation_status=OperationStatus.RUNNING.value,
            shadowbot_run_id=start_result.shadowbot_run_id,
            instruction_hash=str(start_result.raw_output.get("instruction_hash") or instruction_hash),
            request_file_sha256=str(start_result.raw_output.get("request_file_sha256") or ""),
            queue_request_path=str(start_result.raw_output.get("queue_request_path") or ""),
            raw_output=start_result.raw_output,
        )
        if not marked:
            raise ValidationError("SHADOWBOT_LEASE_LOST_DURING_START")
        self._move_task_to_running(payload.task_id, request.execution_attempt_id)
        return ShadowBotExecutorStartResult(
            operation_id=payload.operation_id,
            execution_attempt_id=request.execution_attempt_id,
            shadowbot_run_id=start_result.shadowbot_run_id,
            status=STATUS_RUNNING,
            side_effect_state=SIDE_EFFECT_NOT_STARTED,
        )

    def start_multi_product_read(
        self,
        *,
        task_id: str,
        execution_attempt_id: str,
        request_payload: dict[str, Any],
        lock_owner: str = SHADOWBOT_EXECUTOR_NAME,
        lease_seconds: int = 900,
        runner_payload: dict[str, Any] | None = None,
    ) -> ShadowBotExecutorStartResult:
        """Start one v2 single-platform multi-product READ_ONLY attempt.

        READ_ONLY has no business write approval envelope.  It still gets a
        normal operation/attempt/lease record so the existing queue, phase,
        importer, and execution-log boundaries remain authoritative.
        """

        self.repository.init_schema()
        request_payload = dict(request_payload or {})
        if not str(request_payload.get("read_batch_id") or "").strip():
            request_payload["read_batch_id"] = build_read_batch_id()
        normalized = normalize_multi_product_request(request_payload)
        if str(task_id or "").strip() == "":
            raise ValidationError("task_id is required.")
        if self.repository.get_task(task_id) is None:
            raise ValidationError("task_id does not exist.")
        execution_attempt_id = str(execution_attempt_id or "").strip()
        if not execution_attempt_id:
            raise ValidationError("execution_attempt_id is required.")
        read_batch_id = normalized["read_batch_id"]
        operation_id = f"READ-{read_batch_id}"
        approved_payload_hash = canonical_request_digest(normalized)
        platform = normalized["products"][0]["platform"]
        product_identity = {
            "multi_product_read": True,
            "read_batch_id": read_batch_id,
            "products": normalized["products"],
        }
        existing = self.repository.get_shadowbot_operation(operation_id)
        if existing is None:
            inserted = self.repository.insert_shadowbot_operation(
                ShadowBotOperationLedger(
                    operation_id=operation_id,
                    task_id=task_id,
                    platform=platform,
                    product_identity=product_identity,
                    expected_old_price=Decimal("0"),
                    target_price=Decimal("0"),
                    status=STATUS_PENDING,
                    approved_payload_hash=approved_payload_hash,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
            if inserted != 1:
                raise ValidationError("read_batch_id already exists.")
        elif (
            existing.task_id != task_id
            or existing.platform != platform
            or existing.product_identity != product_identity
            or existing.approved_payload_hash != approved_payload_hash
        ):
            # A read_batch_id is the idempotency boundary.  Replaying it is
            # safe only when the task, platform, product identity, and
            # normalized request digest are all identical.
            raise ValidationError("READ_BATCH_ID_CONFLICT")
        payload: dict[str, Any] = {
            "schema_version": "shadowbot-request-2.0",
            "contract_version": 2,
            "task_id": task_id,
            "operation_id": operation_id,
            "execution_attempt_id": execution_attempt_id,
            "execution_mode": "READ_ONLY",
            "platform_name": platform,
            "read_batch_id": read_batch_id,
            "products": normalized["products"],
            "limits": normalized["limits"],
            "capture_evidence": normalized["capture_evidence"],
            "applet_uri": str(os.environ.get("SHADOWBOT_APPLET_URI", "")).strip(),
            "created_at": utc_now().isoformat(),
            "expires_at": (utc_now() + timedelta(seconds=max(int(lease_seconds), 1))).isoformat(),
        }
        for key, value in (runner_payload or {}).items():
            if key in {"contract_version", "execution_mode", "task_id", "operation_id", "execution_attempt_id", "read_batch_id", "products", "limits", "capture_evidence"}:
                if value != payload.get(key):
                    raise ValidationError(f"runner payload cannot override approved field: {key}")
                continue
            payload[key] = value
        payload["instruction_hash"] = compute_multi_product_instruction_hash(payload)
        now = utc_now()
        owner_token = f"{lock_owner}:{uuid4().hex}"
        lease_expires_at = now + timedelta(seconds=max(int(lease_seconds), 1))
        attempt = ShadowBotExecutionAttempt(
            execution_attempt_id=execution_attempt_id,
            operation_id=operation_id,
            execution_mode=EXECUTION_MODE_READ_ONLY,
            shadowbot_run_id="",
            status=STATUS_STARTING,
            side_effect_state=SIDE_EFFECT_NOT_STARTED,
            started_at=now,
            instruction_hash=payload["instruction_hash"],
            raw_output={
                "contract_version": 2,
                "read_batch_id": read_batch_id,
                "operation_status_before_attempt": STATUS_PENDING,
            },
        )
        claimed = self.repository.create_shadowbot_attempt_with_lease(
            attempt,
            owner_token=owner_token,
            lease_expires_at=lease_expires_at,
            expected_operation_statuses=(STATUS_PENDING,),
        )
        if claimed is None:
            raise ValidationError("read operation is already locked or execution_attempt_id exists.")
        lease = claimed.raw_output.get("lease", {})
        lease_version = int(lease.get("version") or 0)
        payload.update(
            {
                "lease_owner_token": owner_token,
                "lease_version": lease_version,
                "lease_expires_at": str(lease.get("expires_at") or ""),
            }
        )
        try:
            start_result = self.runner.start(payload)
        except Exception as exc:
            boundary_known = isinstance(exc, ShadowBotStartBoundaryError)
            published = exc.published if boundary_known else None
            attempt_status = STATUS_START_FAILED if boundary_known and not published else STATUS_START_UNKNOWN
            operation_status = STATUS_NEEDS_RECONCILIATION if attempt_status == STATUS_START_UNKNOWN else STATUS_FAILED
            raw_output = {
                "error_code": "RUNNER_START_UNKNOWN" if attempt_status == STATUS_START_UNKNOWN else "RUNNER_START_FAILED",
                "error_type": type(exc).__name__,
                "published": published,
                "read_batch_id": read_batch_id,
            }
            if boundary_known:
                raw_output.update(exc.raw_output)
            if not self.repository.mark_shadowbot_start_outcome(
                execution_attempt_id,
                owner_token=owner_token,
                lease_version=lease_version,
                attempt_status=attempt_status,
                operation_status=operation_status,
                instruction_hash=payload["instruction_hash"],
                raw_output=raw_output,
                ended_at=utc_now(),
            ):
                raise ValidationError("SHADOWBOT_LEASE_LOST_DURING_START") from exc
            raise
        if not start_result.shadowbot_run_id:
            self.repository.mark_shadowbot_start_outcome(
                execution_attempt_id,
                owner_token=owner_token,
                lease_version=lease_version,
                attempt_status=STATUS_START_UNKNOWN,
                operation_status=STATUS_NEEDS_RECONCILIATION,
                instruction_hash=payload["instruction_hash"],
                raw_output={"error_code": "RUNNER_START_ID_UNKNOWN", "read_batch_id": read_batch_id},
                ended_at=utc_now(),
            )
            raise ValidationError("ShadowBot runner did not return shadowbot_run_id.")
        if not self.repository.mark_shadowbot_start_outcome(
            execution_attempt_id,
            owner_token=owner_token,
            lease_version=lease_version,
            attempt_status=STATUS_RUNNING,
            operation_status=OperationStatus.RUNNING.value,
            shadowbot_run_id=start_result.shadowbot_run_id,
            instruction_hash=str(start_result.raw_output.get("instruction_hash") or payload["instruction_hash"]),
            request_file_sha256=str(start_result.raw_output.get("request_file_sha256") or ""),
            queue_request_path=str(start_result.raw_output.get("queue_request_path") or ""),
            raw_output=start_result.raw_output,
        ):
            raise ValidationError("SHADOWBOT_LEASE_LOST_DURING_START")
        self._move_task_to_running(task_id, execution_attempt_id)
        return ShadowBotExecutorStartResult(
            operation_id=operation_id,
            execution_attempt_id=execution_attempt_id,
            shadowbot_run_id=start_result.shadowbot_run_id,
            status=STATUS_RUNNING,
            side_effect_state=SIDE_EFFECT_NOT_STARTED,
        )

    def record_side_effect_checkpoint(
        self,
        *,
        operation_id: str,
        execution_attempt_id: str,
        side_effect_state: str,
        lease_owner_token: str = "",
        lease_version: int = 0,
    ):
        _validate_side_effect_state(side_effect_state)
        attempt = self.repository.get_shadowbot_execution_attempt(execution_attempt_id)
        if attempt is None or attempt.operation_id != operation_id:
            raise ValidationError("execution_attempt_id does not belong to operation_id.")
        if isinstance(attempt.raw_output.get("lease"), dict) and not self.repository.validate_shadowbot_lease(
            execution_attempt_id,
            owner_token=lease_owner_token,
            lease_version=lease_version,
            now=utc_now(),
        ):
            raise ValidationError("SHADOWBOT_LEASE_LOST_WRITEBACK_REJECTED")
        return self.repository.insert_shadowbot_side_effect_checkpoint(
            operation_id=operation_id,
            execution_attempt_id=execution_attempt_id,
            side_effect_state=side_effect_state,
            checkpoint_at=utc_now(),
        )

    def renew_lease(
        self,
        *,
        execution_attempt_id: str,
        owner_token: str,
        lease_version: int,
        lease_seconds: int = 900,
    ) -> bool:
        return self.repository.renew_shadowbot_lease(
            execution_attempt_id,
            owner_token=owner_token,
            lease_version=lease_version,
            lease_seconds=max(int(lease_seconds), 1),
        )

    def record_result(
        self,
        result: ShadowBotResultContract,
        *,
        automatic_reconcile_payload: dict[str, Any] | None = None,
    ) -> None:
        self.repository.init_schema()
        normalized_side_effect, legacy_side_effect = normalize_side_effect_state(result.side_effect_state)
        normalized_status, legacy_status = normalize_result_status(result.status, normalized_side_effect)
        if legacy_status or legacy_side_effect:
            normalized_raw = dict(result.raw_output)
            normalized_raw["legacy_state_normalization"] = {
                "result_status": legacy_status,
                "side_effect_state": legacy_side_effect,
            }
            result = replace(
                result,
                status=normalized_status,
                side_effect_state=normalized_side_effect,
                raw_output=normalized_raw,
            )
        _validate_result_contract(result)
        attempt = self.repository.get_shadowbot_execution_attempt(result.execution_attempt_id)
        if attempt is None:
            raise ValidationError("execution_attempt_id does not exist.")
        self._validate_result_binding(attempt, result)
        merged_raw_output = dict(attempt.raw_output)
        merged_raw_output.update(result.raw_output)
        if result.result_id:
            merged_raw_output["result_id"] = result.result_id
        operation_status = _operation_status_from_result(result)
        if result.status in {STATUS_READ_COMPLETED, STATUS_PREVIEW_COMPLETED}:
            operation_status = str(
                attempt.raw_output.get("operation_status_before_attempt") or OperationStatus.PENDING.value
            )
        completed_at = utc_now()
        lease_required = isinstance(attempt.raw_output.get("lease"), dict)
        if lease_required:
            if not result.lease_owner_token or result.lease_version <= 0:
                raise ValidationError("RESULT_CONTRACT_INVALID: lease owner token/version are required.")
            if not self.repository.complete_shadowbot_attempt_with_lease(
                result.execution_attempt_id,
                owner_token=result.lease_owner_token,
                lease_version=result.lease_version,
                attempt_status=attempt_status_from_result(result.status),
                operation_status=operation_status,
                side_effect_state=result.side_effect_state,
                ended_at=completed_at,
                raw_output=merged_raw_output,
            ):
                raise ValidationError("SHADOWBOT_LEASE_LOST_WRITEBACK_REJECTED")
        else:
            self.record_side_effect_checkpoint(
                operation_id=attempt.operation_id,
                execution_attempt_id=attempt.execution_attempt_id,
                side_effect_state=result.side_effect_state,
            )
            self.repository.update_shadowbot_execution_attempt(
                result.execution_attempt_id,
                status=attempt_status_from_result(result.status),
                side_effect_state=result.side_effect_state,
                ended_at=completed_at,
                raw_output=merged_raw_output,
            )
            self.repository.update_shadowbot_operation_status(
                attempt.operation_id,
                operation_status,
                lock_owner="",
            )
        operation = self.repository.get_shadowbot_operation(attempt.operation_id)
        if operation is None:
            raise ValidationError("operation_id does not exist.")
        self._resolve_login_verification_handoff(
            execution_attempt_id=attempt.execution_attempt_id,
            result=result,
        )
        if (
            result.status in {STATUS_START_UNKNOWN, STATUS_SIDE_EFFECT_UNKNOWN}
            and attempt.execution_mode == EXECUTION_MODE_COMMIT
            and not str(result.raw_output.get("price_batch_id") or "").strip()
        ):
            try:
                reconcile = self.ensure_reconcile_attempt(
                    operation_id=operation.operation_id,
                    source_execution_attempt_id=attempt.execution_attempt_id,
                    runner_payload=automatic_reconcile_payload,
                )
                if reconcile is not None:
                    result.raw_output["automatic_reconcile_attempt_id"] = reconcile.execution_attempt_id
                    merged_raw_output["automatic_reconcile_attempt_id"] = reconcile.execution_attempt_id
            except (ValidationError, OSError) as exc:
                result.raw_output["automatic_reconcile_error"] = str(exc)
                merged_raw_output["automatic_reconcile_error"] = str(exc)
                self.repository.update_shadowbot_operation_status(
                    operation.operation_id,
                    STATUS_NEEDS_RECONCILIATION,
                    lock_owner="",
                )
            self.repository.update_shadowbot_execution_attempt(
                result.execution_attempt_id,
                raw_output=merged_raw_output,
            )
        self._insert_execution_log(operation=operation, attempt=attempt, result=result)
        self._update_task_after_result(operation=operation, result=result)

    def open_login_verification_handoff(self, phase_data: dict[str, Any]) -> str:
        """Create one auditable manual-verification record for a waiting Worker attempt."""
        execution_attempt_id = str(phase_data.get("execution_attempt_id") or "").strip()
        if not execution_attempt_id:
            raise ValidationError("LOGIN_VERIFICATION_REQUIRED phase has no execution_attempt_id.")
        attempt = self.repository.get_shadowbot_execution_attempt(execution_attempt_id)
        if attempt is None:
            raise ValidationError("LOGIN_VERIFICATION_REQUIRED attempt does not exist.")
        review_task_id = _login_verification_review_id(execution_attempt_id)
        existing = self.repository.get_review_task(review_task_id)
        if existing is not None:
            return review_task_id
        operation = self.repository.get_shadowbot_operation(attempt.operation_id)
        if operation is None:
            raise ValidationError("LOGIN_VERIFICATION_REQUIRED operation does not exist.")
        login = phase_data.get("login") if isinstance(phase_data.get("login"), dict) else {}
        now = utc_now()
        requested_deadline = _parse_optional_datetime(login.get("verification_deadline_at"))
        if requested_deadline is None:
            deadline = now + timedelta(minutes=5)
        else:
            if requested_deadline.tzinfo is None:
                requested_deadline = requested_deadline.replace(tzinfo=now.tzinfo)
            ttl_seconds = (requested_deadline - now).total_seconds()
            # Leave one second of scheduling headroom so validation performed
            # immediately afterward still observes the 120..600 second TTL.
            deadline = now + timedelta(seconds=min(599, max(121, ttl_seconds)))
        review = ReviewTask(
            review_task_id=review_task_id,
            trade_date=now.date(),
            scope_type="task",
            scope_key=attempt.execution_attempt_id,
            dedupe_key="shadowbot-login-verification|" + attempt.execution_attempt_id,
            source_task_id=operation.task_id,
            review_type=LOGIN_VERIFICATION_REVIEW_TYPE,
            review_status=ReviewTaskStatus.PENDING,
            internal_sku=str(operation.product_identity.get("internal_sku") or "") or None,
            platform_name=operation.platform,
            reason="ShadowBot is waiting for manual phone verification in the desktop mini program.",
            review_payload={
                "operation_id": operation.operation_id,
                "execution_attempt_id": attempt.execution_attempt_id,
                "execution_mode": attempt.execution_mode,
                "verification_detected_at": str(login.get("verification_detected_at") or ""),
                "verification_deadline_at": deadline.isoformat(),
                "verification_markers": list(login.get("verification_markers") or [])[:5],
            },
            required_by=deadline,
            created_at=now,
            updated_at=now,
        )
        self.notification_outbox_service.create_verification_review_task_atomically(
            review,
            operation_id=operation.operation_id,
            attempt_id=attempt.execution_attempt_id,
            payload={
                "platform_name": review.platform_name or "-",
                "required_by": deadline.isoformat(),
                "verification_markers": list(login.get("verification_markers") or [])[:5],
            },
        )
        return review_task_id

    def _resolve_login_verification_handoff(
        self,
        *,
        execution_attempt_id: str,
        result: ShadowBotResultContract,
    ) -> None:
        review = self.repository.get_review_task(_login_verification_review_id(execution_attempt_id))
        if review is None or review.review_status != ReviewTaskStatus.PENDING:
            return
        login = result.raw_output.get("login") if isinstance(result.raw_output.get("login"), dict) else {}
        if login.get("verification_completed"):
            status = ReviewTaskStatus.APPROVED
            note = "phone verification observed as completed by ShadowBot"
        elif result.error_code == "LOGIN_VERIFICATION_TIMEOUT":
            status = ReviewTaskStatus.EXPIRED
            note = "phone verification timed out"
        elif result.error_code == "WORKER_STOP_REQUESTED":
            status = ReviewTaskStatus.CANCELLED
            note = "Worker stopped while waiting for phone verification"
        else:
            return
        self.repository.update_review_task(
            replace(
                review,
                review_status=status,
                resolution_payload={"execution_attempt_id": execution_attempt_id, "error_code": result.error_code},
                updated_at=utc_now(),
                resolved_by=SHADOWBOT_EXECUTOR_NAME,
                resolved_at=utc_now(),
                resolution_note=note,
            )
        )

    def _validate_result_binding(
        self,
        attempt: ShadowBotExecutionAttempt,
        result: ShadowBotResultContract,
    ) -> None:
        if result.operation_id and result.operation_id != attempt.operation_id:
            raise ValidationError("RESULT_CONTRACT_INVALID: operation_id mismatch.")
        if result.execution_mode and result.execution_mode != attempt.execution_mode:
            raise ValidationError("RESULT_CONTRACT_INVALID: execution_mode mismatch.")
        if attempt.request_file_sha256 and result.instruction_hash != attempt.instruction_hash:
            raise ValidationError("RESULT_CONTRACT_INVALID: instruction_hash mismatch.")
        if attempt.request_file_sha256 and result.request_file_sha256 != attempt.request_file_sha256:
            raise ValidationError("RESULT_CONTRACT_INVALID: request_file_sha256 mismatch.")
        operation = self.repository.get_shadowbot_operation(attempt.operation_id)
        if operation is not None and result.task_id and result.task_id != operation.task_id:
            raise ValidationError("RESULT_CONTRACT_INVALID: task_id mismatch.")

    def start_reconcile_attempt(
        self,
        *,
        operation_id: str,
        execution_attempt_id: str,
        lock_owner: str = SHADOWBOT_EXECUTOR_NAME,
        runner_payload: dict[str, Any] | None = None,
    ) -> ShadowBotExecutorStartResult:
        self.repository.init_schema()
        _reject_fault_injection(runner_payload or {})
        operation = self.repository.get_shadowbot_operation(operation_id)
        if operation is None:
            raise ValidationError("operation_id does not exist.")
        if operation.status != STATUS_NEEDS_RECONCILIATION:
            raise ValidationError("operation does not require reconciliation.")
        payload = _build_queue_request_payload(
            operation_id=operation.operation_id,
            task_id=operation.task_id,
            execution_attempt_id=execution_attempt_id,
            execution_mode=EXECUTION_MODE_RECONCILE,
            platform_name=operation.platform,
            product_identity=operation.product_identity,
            expected_old_price=operation.expected_old_price,
            target_price=operation.target_price,
            approved_payload_hash=operation.approved_payload_hash,
            approval_id="",
            approval_expires_at=None,
            overrides=runner_payload or {},
        )
        instruction_hash = str(payload["instruction_hash"])
        now = utc_now()
        owner_token = f"{lock_owner}:{uuid4().hex}"
        lease_expires_at = now + timedelta(seconds=900)
        attempt = ShadowBotExecutionAttempt(
            execution_attempt_id=execution_attempt_id,
            operation_id=operation_id,
            execution_mode=EXECUTION_MODE_RECONCILE,
            shadowbot_run_id="",
            status=STATUS_STARTING,
            side_effect_state=SIDE_EFFECT_NOT_STARTED,
            started_at=now,
            instruction_hash=instruction_hash,
            raw_output={"source_execution_attempt_id": str(payload.get("source_execution_attempt_id") or "")},
        )
        claimed_attempt = self.repository.create_shadowbot_attempt_with_lease(
            attempt,
            owner_token=owner_token,
            lease_expires_at=lease_expires_at,
            expected_operation_statuses=(STATUS_NEEDS_RECONCILIATION,),
        )
        if claimed_attempt is None:
            raise ValidationError("ShadowBot operation is already locked or execution_attempt_id exists.")
        lease = claimed_attempt.raw_output.get("lease", {})
        lease_version = int(lease.get("version") or 0)
        payload.update(
            {
                "lease_owner_token": owner_token,
                "lease_version": lease_version,
                "lease_expires_at": str(lease.get("expires_at") or ""),
            }
        )
        try:
            start_result = self.runner.start(payload)
        except Exception as exc:
            boundary_known = isinstance(exc, ShadowBotStartBoundaryError)
            published = exc.published if boundary_known else None
            attempt_status = STATUS_START_FAILED if boundary_known and not published else STATUS_START_UNKNOWN
            self.repository.mark_shadowbot_start_outcome(
                execution_attempt_id,
                owner_token=owner_token,
                lease_version=lease_version,
                attempt_status=attempt_status,
                operation_status=STATUS_NEEDS_RECONCILIATION,
                instruction_hash=instruction_hash,
                ended_at=utc_now(),
                raw_output={
                    "error_code": "RECONCILE_START_FAILED" if attempt_status == STATUS_START_FAILED else "RECONCILE_START_UNKNOWN",
                    "error_type": type(exc).__name__,
                    "published": published,
                },
            )
            raise
        if not start_result.shadowbot_run_id:
            self.repository.mark_shadowbot_start_outcome(
                execution_attempt_id,
                owner_token=owner_token,
                lease_version=lease_version,
                attempt_status=STATUS_START_UNKNOWN,
                operation_status=STATUS_NEEDS_RECONCILIATION,
                instruction_hash=instruction_hash,
                ended_at=utc_now(),
                raw_output={"error_code": "RECONCILE_START_ID_UNKNOWN"},
            )
            raise ValidationError("ShadowBot runner did not return shadowbot_run_id.")
        marked = self.repository.mark_shadowbot_start_outcome(
            execution_attempt_id,
            owner_token=owner_token,
            lease_version=lease_version,
            attempt_status=STATUS_RUNNING,
            operation_status=OperationStatus.RUNNING.value,
            shadowbot_run_id=start_result.shadowbot_run_id,
            instruction_hash=str(start_result.raw_output.get("instruction_hash") or instruction_hash),
            request_file_sha256=str(start_result.raw_output.get("request_file_sha256") or ""),
            queue_request_path=str(start_result.raw_output.get("queue_request_path") or ""),
            raw_output=start_result.raw_output,
        )
        if not marked:
            raise ValidationError("SHADOWBOT_LEASE_LOST_DURING_START")
        return ShadowBotExecutorStartResult(
            operation_id=operation_id,
            execution_attempt_id=execution_attempt_id,
            shadowbot_run_id=start_result.shadowbot_run_id,
            status=STATUS_RUNNING,
            side_effect_state=SIDE_EFFECT_NOT_STARTED,
        )

    def ensure_reconcile_attempt(
        self,
        *,
        operation_id: str,
        source_execution_attempt_id: str,
        runner_payload: dict[str, Any] | None = None,
    ) -> ShadowBotExecutorStartResult | None:
        digest = hashlib.sha256(source_execution_attempt_id.encode("utf-8")).hexdigest()[:20]
        execution_attempt_id = f"RECONCILE-{digest}"
        existing = self.repository.get_shadowbot_execution_attempt(execution_attempt_id)
        if existing is not None:
            return ShadowBotExecutorStartResult(
                operation_id=existing.operation_id,
                execution_attempt_id=existing.execution_attempt_id,
                shadowbot_run_id=existing.shadowbot_run_id,
                status=existing.status,
                side_effect_state=existing.side_effect_state,
            )
        operation = self.repository.get_shadowbot_operation(operation_id)
        if operation is None:
            raise ValidationError("operation_id does not exist.")
        if operation.status != STATUS_NEEDS_RECONCILIATION:
            return None
        return self.start_reconcile_attempt(
            operation_id=operation_id,
            execution_attempt_id=execution_attempt_id,
            runner_payload={
                "source_execution_attempt_id": source_execution_attempt_id,
                "triggered_by": SHADOWBOT_EXECUTOR_NAME,
                "triggered_from": "automatic_reconciliation",
                **(runner_payload or {}),
            },
        )

    def confirm_manual_handled(self, *, operation_id: str, actor: str, note: str = "") -> None:
        self.repository.init_schema()
        operation = self.repository.get_shadowbot_operation(operation_id)
        if operation is None:
            raise ValidationError("operation_id does not exist.")
        self.repository.update_shadowbot_operation_status(operation_id, "MANUAL_HANDLED", lock_owner="")
        now = utc_now()
        self.repository.insert_execution_logs(
            [
                ExecutionLog(
                    log_id=f"shadowbot-manual-{uuid4().hex[:12]}",
                    task_id=operation.task_id,
                    executor_name=SHADOWBOT_EXECUTOR_NAME,
                    start_time=now,
                    end_time=now,
                    success_flag=True,
                    error_code="",
                    error_message="manual handling confirmed",
                    raw_output=json.dumps(
                        {
                            "operation_id": operation_id,
                            "status": "MANUAL_HANDLED",
                            "manual_actor": actor,
                            "manual_note": note,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    created_at=now,
                )
            ]
        )

    def classify_timeout(
        self,
        operation_id: str,
        *,
        now: datetime | None = None,
    ) -> ShadowBotTimeoutClassification:
        """Fence a genuinely expired active attempt; never mutate only the operation."""
        current = now or utc_now()
        active = [
            attempt
            for attempt in self.repository.list_active_shadowbot_execution_attempts()
            if attempt.operation_id == operation_id and attempt.execution_mode == EXECUTION_MODE_COMMIT
        ]
        if len(active) != 1:
            raise ValidationError("SHADOWBOT_TIMEOUT_REQUIRES_ONE_ACTIVE_COMMIT_ATTEMPT")
        attempt = active[0]
        lease = attempt.raw_output.get("lease") if isinstance(attempt.raw_output.get("lease"), dict) else {}
        lease_expires_at = _parse_optional_datetime(lease.get("expires_at"))
        if not lease or lease_expires_at is None:
            raise ValidationError("SHADOWBOT_TIMEOUT_REQUIRES_LEASE")
        if lease_expires_at > current:
            raise ValidationError("SHADOWBOT_LEASE_STILL_ACTIVE")
        if not self.repository.expire_shadowbot_lease(attempt.execution_attempt_id, now=current):
            raise ValidationError("SHADOWBOT_TIMEOUT_FENCING_CONFLICT")
        expired_attempt = self.repository.get_shadowbot_execution_attempt(attempt.execution_attempt_id)
        if expired_attempt is None:
            raise ValidationError("execution_attempt_id does not exist.")
        side_effect_state = expired_attempt.side_effect_state
        status = STATUS_NEEDS_RECONCILIATION
        retryable = False
        next_mode = EXECUTION_MODE_RECONCILE
        return ShadowBotTimeoutClassification(
            operation_id=operation_id,
            status=status,
            side_effect_state=side_effect_state,
            retryable=retryable,
            next_execution_mode=next_mode,
        )

    def _validate_approval(self, request: ShadowBotExecutionRequest) -> ShadowBotApprovedPayload:
        approval = request.approval
        if not approval.approval_id:
            raise ValidationError("approval_id is required.")
        if approval.approval_status != "APPROVED":
            raise ValidationError("approval is not approved.")
        if approval.expires_at is not None and approval.expires_at <= utc_now():
            raise ValidationError("approval has expired.")
        payload = approval.approved_payload
        if payload.operation_id != request.operation_id:
            raise ValidationError("approval does not belong to this operation.")
        if approval.approval_contract_version == 3:
            if not approval.approved_payload_view:
                raise ValidationError("approved task 12 payload view is required.")
            actual_hash = sha256_jcs(approval.approved_payload_view)
            if actual_hash != _normalize_sha256_text(approval.approved_payload_hash):
                raise ValidationError("approved payload hash mismatch.")
            _validate_task12_approved_payload_view(approval.approved_payload_view, payload)
        else:
            actual_hash = compute_approved_payload_hash(payload)
            if actual_hash != approval.approved_payload_hash:
                raise ValidationError("approved payload hash mismatch.")
        review_task = self.repository.get_review_task(approval.approval_id)
        if review_task is None:
            raise ValidationError("approval record does not exist.")
        if review_task.review_status != ReviewTaskStatus.APPROVED:
            raise ValidationError("approval record is not approved.")
        if review_task.source_task_id and review_task.source_task_id != payload.task_id:
            raise ValidationError("approval record does not belong to this task.")
        operation = self.repository.get_shadowbot_operation(payload.operation_id)
        approved_platform_names = {
            normalize_text(value)
            for value in (
                operation.product_identity.get("approved_platform_names", [])
                if operation is not None
                else []
            )
            if normalize_text(value)
        }
        approved_platform_names.add(normalize_text(payload.platform))
        if review_task.platform_name and normalize_text(review_task.platform_name) not in approved_platform_names:
            raise ValidationError("approval record does not belong to this platform.")
        approved_sku = str(
            payload.product_identity.get("sku")
            or payload.product_identity.get("internal_sku")
            or payload.product_identity.get("platform_sku")
            or ""
        )
        if review_task.internal_sku and approved_sku and review_task.internal_sku != approved_sku:
            raise ValidationError("approval record does not belong to this SKU.")
        task = self.repository.get_task(payload.task_id)
        if task is None:
            raise ValidationError("approved task does not exist.")
        if task.platform_name and normalize_text(task.platform_name) not in approved_platform_names:
            raise ValidationError("approved task platform does not match payload.")
        if task.internal_sku and approved_sku and task.internal_sku != approved_sku:
            raise ValidationError("approved task SKU does not match payload.")
        stored_hash = str(
            review_task.resolution_payload.get("approved_payload_hash")
            or review_task.review_payload.get("approved_payload_hash")
            or ""
        )
        if approval.approval_contract_version == 3:
            stored_hash_matches = _normalize_sha256_text(stored_hash) == _normalize_sha256_text(
                approval.approved_payload_hash
            )
        else:
            stored_hash_matches = stored_hash == approval.approved_payload_hash
        if not stored_hash_matches:
            raise ValidationError("approval record hash does not match approved payload.")
        return payload

    def _ensure_operation(self, payload: ShadowBotApprovedPayload, approved_payload_hash: str) -> None:
        existing = self.repository.get_shadowbot_operation(payload.operation_id)
        if existing is None:
            inserted = self.repository.insert_shadowbot_operation(
                ShadowBotOperationLedger(
                    operation_id=payload.operation_id,
                    task_id=payload.task_id,
                    platform=payload.platform,
                    product_identity=payload.product_identity,
                    expected_old_price=payload.expected_old_price,
                    target_price=payload.target_price,
                    status=STATUS_PENDING,
                    approved_payload_hash=approved_payload_hash,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
            if inserted != 1:
                raise ValidationError("operation_id already exists.")
            return
        if existing.approved_payload_hash != approved_payload_hash:
            raise ValidationError("operation_id already exists with a different approved payload.")

    def _requires_reconciliation(self, operation_id: str) -> bool:
        checkpoint = self.repository.latest_shadowbot_side_effect_checkpoint(operation_id)
        return checkpoint is not None and checkpoint.side_effect_state in SIDE_EFFECT_RECONCILE_REQUIRED

    def _move_task_to_running(self, task_id: str, execution_attempt_id: str) -> None:
        task = self.repository.get_task(task_id)
        if task is None:
            raise ValidationError("approved task does not exist.")
        if task.task_status == TaskStatus.RUNNING:
            return
        if task.task_status == TaskStatus.PENDING:
            self.runtime_task_service.change_status(
                task_id=task_id,
                to_status=TaskStatus.RUNNING,
                changed_by=SHADOWBOT_EXECUTOR_NAME,
                reason="shadowbot_start",
                metadata={"execution_attempt_id": execution_attempt_id},
            )

    def _insert_execution_log(
        self,
        *,
        operation: ShadowBotOperationLedger,
        attempt: ShadowBotExecutionAttempt,
        result: ShadowBotResultContract,
    ) -> None:
        raw_output = dict(result.raw_output)
        raw_output.setdefault("operation_id", operation.operation_id)
        raw_output.setdefault("execution_attempt_id", result.execution_attempt_id)
        raw_output.setdefault("shadowbot_run_id", attempt.shadowbot_run_id)
        raw_output.setdefault("execution_mode", attempt.execution_mode)
        raw_output.setdefault("instruction_hash", attempt.instruction_hash)
        raw_output.setdefault("request_file_sha256", attempt.request_file_sha256)
        raw_output.setdefault("queue_request_path", attempt.queue_request_path)
        raw_output.setdefault("status", result.status)
        raw_output.setdefault("side_effect_state", result.side_effect_state)
        raw_output.setdefault("expected_old_price", serialize_decimal(operation.expected_old_price))
        raw_output.setdefault("target_price", serialize_decimal(operation.target_price))
        raw_output.setdefault("approved_payload_hash", operation.approved_payload_hash)
        self.repository.insert_execution_logs(
            [
                ExecutionLog(
                    log_id=f"shadowbot-{uuid4().hex[:12]}",
                    task_id=operation.task_id,
                    executor_name=SHADOWBOT_EXECUTOR_NAME,
                    start_time=attempt.started_at,
                    end_time=utc_now(),
                    success_flag=result.run_success_flag,
                    error_code=result.error_code,
                    error_message=str(raw_output.get("error_message") or result.error_code or result.status),
                    raw_output=json.dumps(raw_output, ensure_ascii=False, sort_keys=True, default=str),
                    created_at=utc_now(),
                )
            ]
        )

    def _update_task_after_result(self, *, operation: ShadowBotOperationLedger, result: ShadowBotResultContract) -> None:
        task = self.repository.get_task(operation.task_id)
        if task is None:
            return
        if result.business_operation_completed is True:
            if task.task_status != TaskStatus.SUCCESS:
                if task.task_status == TaskStatus.PENDING:
                    self._move_task_to_running(operation.task_id, result.execution_attempt_id)
                self.runtime_task_service.change_status(
                    task_id=operation.task_id,
                    to_status=TaskStatus.SUCCESS,
                    changed_by=SHADOWBOT_EXECUTOR_NAME,
                    reason="shadowbot_business_completed",
                    metadata={
                        "operation_id": operation.operation_id,
                        "execution_attempt_id": result.execution_attempt_id,
                        "shadowbot_status": result.status,
                        "side_effect_state": result.side_effect_state,
                    },
                    result_message=f"ShadowBot {result.status}",
                )
            return
        if result.status == STATUS_FAILED and result.side_effect_state == SIDE_EFFECT_NOT_STARTED:
            if task.task_status == TaskStatus.RUNNING:
                self.runtime_task_service.change_status(
                    task_id=operation.task_id,
                    to_status=TaskStatus.FAILED,
                    changed_by=SHADOWBOT_EXECUTOR_NAME,
                    reason="shadowbot_pre_submit_failed",
                    metadata={
                        "operation_id": operation.operation_id,
                        "execution_attempt_id": result.execution_attempt_id,
                        "error_code": result.error_code,
                        "retryable": result.retryable,
                    },
                    result_message=result.error_code or "ShadowBot FAILED",
                )


def compute_approved_payload_hash(payload: ShadowBotApprovedPayload) -> str:
    canonical = {
        "operation_id": payload.operation_id,
        "task_id": payload.task_id,
        "platform": payload.platform,
        "product_identity": payload.product_identity,
        "expected_old_price": serialize_decimal(payload.expected_old_price),
        "target_price": serialize_decimal(payload.target_price),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_sha256_text(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith("sha256:"):
        digest = raw[7:]
    else:
        digest = raw
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValidationError("approved payload hash is not SHA-256.")
    return "sha256:" + digest


def _validate_task12_approved_payload_view(
    view: dict[str, Any],
    payload: ShadowBotApprovedPayload,
) -> None:
    identity = view.get("product_identity")
    if not isinstance(identity, dict):
        raise ValidationError("approved task 12 product identity is invalid.")
    payload_name = payload.product_identity.get("expected_product_name") or payload.product_identity.get("name")
    payload_grade = payload.product_identity.get("expected_grade") or payload.product_identity.get("grade")
    payload_sku = payload.product_identity.get("platform_sku") or payload.product_identity.get("sku")
    if (
        view.get("v") != 3
        or str(view.get("operation_id") or "") != payload.operation_id
        or str(view.get("task_id") or "") != payload.task_id
        or normalize_text(view.get("platform")) != normalize_text(payload.platform)
        or normalize_text(identity.get("normalized_product_name")) != normalize_text(payload_name)
        or normalize_grade(identity.get("normalized_grade")) != normalize_grade(payload_grade)
        or normalize_sku(identity.get("platform_sku")) != normalize_sku(payload_sku)
        or str(view.get("approved_expected_old_price") or "")
        != serialize_decimal(payload.expected_old_price)
        or str(view.get("target_price") or "") != serialize_decimal(payload.target_price)
    ):
        raise ValidationError("approved task 12 payload view does not match execution payload.")


INSTRUCTION_HASH_FIELDS = (
    "task_id",
    "operation_id",
    "execution_attempt_id",
    "execution_mode",
    "platform_name",
    "platform_sku",
    "product_keyword",
    "expected_product_name",
    "expected_grade",
    "expected_spec",
    "spec_verification_required",
    "expected_old_price",
    "target_price",
    "applet_uri",
)

TASK12_INSTRUCTION_HASH_FIELDS = (
    "batch_contract_version",
    "price_batch_id",
    "price_batch_item_id",
    "price_batch_ordinal",
    "price_batch_stage",
    "batch_execution_mode",
    "normalized_request_digest",
    "source_read_batch_id",
    "source_snapshot_sha256",
    "source_page_context_sha256",
    "page_identity_key",
    "write_identity_key",
    "fresh_read_attempt_id",
    "fresh_read_result_sha256",
    "fresh_old_price",
    "approved_payload_hash",
    "approval_id",
    "expires_at",
    "capture_evidence",
)


def compute_instruction_hash(payload: dict[str, Any]) -> str:
    fields = INSTRUCTION_HASH_FIELDS
    if payload.get("batch_contract_version") == 3:
        fields = INSTRUCTION_HASH_FIELDS + TASK12_INSTRUCTION_HASH_FIELDS
    canonical = {field_name: payload.get(field_name, "") for field_name in fields}
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _build_queue_request_payload(
    *,
    operation_id: str,
    task_id: str,
    execution_attempt_id: str,
    execution_mode: str,
    platform_name: str,
    product_identity: dict[str, Any],
    expected_old_price: Decimal,
    target_price: Decimal,
    approved_payload_hash: str,
    approval_id: str,
    approval_expires_at: datetime | None,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    _reject_fault_injection(overrides)
    platform_sku = str(product_identity.get("platform_sku") or product_identity.get("sku") or "").strip()
    expected_name = str(
        product_identity.get("expected_product_name") or product_identity.get("name") or ""
    ).strip()
    expected_grade = str(product_identity.get("expected_grade") or product_identity.get("grade") or "").strip()
    expected_spec = str(product_identity.get("expected_spec") or product_identity.get("spec") or "").strip()
    product_keyword = str(overrides.get("product_keyword") or f"{expected_grade}{expected_name}").strip()
    now = utc_now()
    default_expiry = now + timedelta(minutes=30)
    expires_at = min(approval_expires_at, default_expiry) if approval_expires_at else default_expiry
    payload: dict[str, Any] = {
        "schema_version": "shadowbot-request-1.0",
        "task_id": task_id,
        "operation_id": operation_id,
        "execution_attempt_id": execution_attempt_id,
        "execution_mode": execution_mode,
        "platform_name": platform_name,
        "platform_sku": platform_sku,
        "product_keyword": product_keyword,
        "expected_product_name": expected_name,
        "expected_grade": expected_grade,
        "expected_spec": expected_spec,
        "spec_verification_required": bool(overrides.get("spec_verification_required", False)),
        "expected_old_price": serialize_decimal(expected_old_price),
        "target_price": serialize_decimal(target_price),
        "approved_payload_hash": approved_payload_hash,
        "approval_id": approval_id,
        "applet_uri": str(overrides.get("applet_uri") or os.environ.get("SHADOWBOT_APPLET_URI", "")).strip(),
        "evidence_share_dir": str(
            overrides.get("evidence_share_dir") or os.environ.get("SHADOWBOT_EVIDENCE_DIR", "")
        ).strip(),
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    immutable_fields = {
        "task_id",
        "operation_id",
        "execution_attempt_id",
        "execution_mode",
        "platform_name",
        "platform_sku",
        "expected_product_name",
        "expected_grade",
        "expected_old_price",
        "target_price",
        "approved_payload_hash",
    }
    for key, value in overrides.items():
        if key in immutable_fields:
            if str(value) != str(payload[key]):
                raise ValidationError(f"runner payload cannot override approved field: {key}")
            continue
        if key not in {"fault_injection", "instruction_hash"}:
            payload[key] = value
    payload["instruction_hash"] = compute_instruction_hash(payload)
    return payload


def _reject_fault_injection(payload: dict[str, Any]) -> None:
    if str(payload.get("fault_injection") or "").strip():
        raise ValidationError("UNSAFE_TEST_PARAMETER_REJECTED: fault_injection is not allowed by production Executor.")


def _validate_queue_request(payload: dict[str, Any]) -> None:
    _reject_fault_injection(payload)
    required = (
        "task_id",
        "operation_id",
        "execution_attempt_id",
        "execution_mode",
        "platform_name",
        "platform_sku",
        "product_keyword",
        "expected_product_name",
        "expected_grade",
        "expected_old_price",
        "target_price",
        "approved_payload_hash",
        "created_at",
        "expires_at",
    )
    missing = [field_name for field_name in required if not str(payload.get(field_name) or "").strip()]
    if payload.get("batch_contract_version") == 3:
        missing = [field_name for field_name in missing if field_name != "platform_sku"]
    if missing:
        raise ValidationError("ShadowBot queue request is missing required fields: " + ", ".join(missing))
    _validate_execution_mode(str(payload["execution_mode"]))
    if bool(payload.get("spec_verification_required")):
        raise ValidationError("INPUT_INVALID: current platform adapter cannot verify expected_spec.")
    if payload.get("batch_contract_version") == 3:
        _validate_task12_item_queue_request(payload)
    try:
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
    except ValueError as exc:
        raise ValidationError("ShadowBot queue expires_at must be ISO-8601.") from exc
    if expires_at.tzinfo is None:
        raise ValidationError("ShadowBot queue expires_at must include a timezone.")
    if expires_at <= utc_now():
        raise ValidationError("ShadowBot queue request has expired.")


def _validate_task12_item_queue_request(payload: dict[str, Any]) -> None:
    if len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")) > 256 * 1024:
        raise ValidationError("REQUEST_SIZE_LIMIT_EXCEEDED")
    required = tuple(
        name
        for name in TASK12_INSTRUCTION_HASH_FIELDS
        if name not in {"approval_id", "capture_evidence"}
    )
    missing = [name for name in required if not str(payload.get(name) or "").strip()]
    stage = str(payload.get("price_batch_stage") or "").strip().upper()
    if stage == "FRESH_READ":
        missing = [
            name
            for name in missing
            if name not in {"fresh_read_result_sha256", "fresh_old_price"}
        ]
    if missing:
        raise ValidationError("task 12 queue request is missing fields: " + ", ".join(missing))
    ordinal = payload.get("price_batch_ordinal")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        raise ValidationError("INVALID_ORDINAL")
    for name in (
        "normalized_request_digest",
        "source_snapshot_sha256",
        "source_page_context_sha256",
        "page_identity_key",
        "write_identity_key",
        "approved_payload_hash",
    ):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(payload.get(name) or "")):
            raise ValidationError("task 12 queue hash is invalid: " + name)
    if not isinstance(payload.get("capture_evidence"), bool):
        raise ValidationError("capture_evidence must be boolean")
    mode = str(payload.get("execution_mode") or "").strip().upper()
    batch_mode = str(payload.get("batch_execution_mode") or "").strip().upper()
    if stage != "RECONCILE" and not str(payload.get("approval_id") or "").strip():
        raise ValidationError("task 12 queue approval_id is required")
    if batch_mode not in {EXECUTION_MODE_FILL_PREVIEW, EXECUTION_MODE_COMMIT}:
        raise ValidationError("UNSUPPORTED_EXECUTION_MODE")
    if stage == "FRESH_READ":
        if mode != EXECUTION_MODE_READ_ONLY or payload.get("fresh_read_attempt_id") != payload.get(
            "execution_attempt_id"
        ):
            raise ValidationError("BATCH_ITEM_BINDING_MISMATCH")
        if str(payload.get("fresh_read_result_sha256") or "") or str(payload.get("fresh_old_price") or ""):
            raise ValidationError("BATCH_ITEM_BINDING_MISMATCH")
    elif stage == "WRITE":
        if mode != batch_mode:
            raise ValidationError("BATCH_ITEM_BINDING_MISMATCH")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(payload.get("fresh_read_result_sha256") or "")):
            raise ValidationError("BATCH_ITEM_BINDING_MISMATCH")
        if str(payload.get("fresh_old_price") or "") != str(payload.get("expected_old_price") or ""):
            raise ValidationError("OLD_PRICE_CHANGED")
    elif stage == "RECONCILE":
        if mode != EXECUTION_MODE_RECONCILE:
            raise ValidationError("BATCH_ITEM_BINDING_MISMATCH")
    else:
        raise ValidationError("BATCH_ITEM_BINDING_MISMATCH")


def _validate_multi_product_queue_request(payload: dict[str, Any]) -> None:
    """Validate task 11's v2 request without requiring single-product fields."""

    normalized = normalize_multi_product_request(payload)
    required = ("task_id", "operation_id", "execution_attempt_id", "created_at", "expires_at", "instruction_hash")
    missing = [field_name for field_name in required if not str(payload.get(field_name) or "").strip()]
    if missing:
        raise ValidationError("ShadowBot v2 queue request is missing required fields: " + ", ".join(missing))
    try:
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
    except ValueError as exc:
        raise ValidationError("ShadowBot v2 queue expires_at must be ISO-8601.") from exc
    if expires_at.tzinfo is None or expires_at <= utc_now():
        raise ValidationError("ShadowBot v2 queue request has expired.")
    if str(payload.get("instruction_hash")) != compute_multi_product_instruction_hash(payload):
        raise ValidationError("multi-product instruction_hash mismatch")
    if normalized["read_batch_id"] != payload.get("read_batch_id"):
        raise ValidationError("multi-product read_batch_id normalization mismatch")


def _canonical_file_json(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n").encode("utf-8")


def _atomic_publish(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{uuid4().hex}")
    with temporary.open("xb") as file_obj:
        file_obj.write(content)
        file_obj.flush()
        os.fsync(file_obj.fileno())
    os.replace(temporary, path)


def _validate_execution_mode(execution_mode: str) -> None:
    if execution_mode not in ALLOWED_EXECUTION_MODES:
        raise ValidationError(f"unsupported ShadowBot execution_mode: {execution_mode}")


def _validate_side_effect_state(side_effect_state: str) -> None:
    normalize_side_effect_state(side_effect_state)


def _validate_result_contract(result: ShadowBotResultContract) -> None:
    validate_result_state(
        status=result.status,
        side_effect_state=result.side_effect_state,
        run_success_flag=result.run_success_flag,
        business_operation_completed=result.business_operation_completed,
        retryable=result.retryable,
        error_code=result.error_code,
    )


def shadowbot_result_contract_from_data(data: dict[str, Any]) -> ShadowBotResultContract:
    side_effect_state, legacy_side_effect = normalize_side_effect_state(
        _required_contract_text(data, "side_effect_state")
    )
    status, legacy_status = normalize_result_status(_required_contract_text(data, "status"), side_effect_state)
    normalized_data = dict(data)
    if legacy_status or legacy_side_effect:
        normalized_data["legacy_state_normalization"] = {
            "result_status": legacy_status,
            "side_effect_state": legacy_side_effect,
        }
        normalized_data["status"] = status
        normalized_data["side_effect_state"] = side_effect_state
    return ShadowBotResultContract(
        execution_attempt_id=_required_contract_text(data, "execution_attempt_id"),
        status=status,
        run_success_flag=_nullable_contract_bool(data.get("run_success_flag")),
        business_operation_completed=_nullable_contract_bool(data.get("business_operation_completed")),
        side_effect_state=side_effect_state,
        retryable=bool(_nullable_contract_bool(data.get("retryable", False))),
        error_code=str(data.get("error_code") or ""),
        operation_id=str(data.get("operation_id") or ""),
        task_id=str(data.get("task_id") or ""),
        execution_mode=str(data.get("execution_mode") or ""),
        instruction_hash=str(data.get("instruction_hash") or ""),
        request_file_sha256=str(data.get("request_file_sha256") or ""),
        result_id=str(data.get("result_id") or ""),
        lease_owner_token=str(data.get("lease_owner_token") or ""),
        lease_version=int(data.get("lease_version") or 0),
        worker_id=str(data.get("worker_id") or ""),
        raw_output=normalized_data,
    )


def _required_contract_text(data: dict[str, Any], name: str) -> str:
    value = str(data.get(name) or "").strip()
    if not value:
        raise ValidationError(f"ShadowBot result is missing required field: {name}")
    return value


def _nullable_contract_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValidationError("ShadowBot result flag must be true, false, or null.")


def _login_verification_review_id(execution_attempt_id: str) -> str:
    digest = hashlib.sha256(execution_attempt_id.encode("utf-8")).hexdigest()[:20]
    return "LOGIN-VERIFY-" + digest


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=utc_now().tzinfo)
    return parsed


def _operation_status_from_result(result: ShadowBotResultContract) -> str:
    return operation_status_from_result(result.status, result.side_effect_state)


def _request_json(request: Request, timeout: float) -> dict[str, Any]:
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        raw = response.read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValidationError("Yingdao OpenAPI response must be a JSON object.")
    return data


def _require_response_data(response: dict[str, Any], *, endpoint: str) -> dict[str, Any]:
    if not bool(response.get("success")):
        message = str(response.get("msg") or response.get("message") or "unknown error")
        code = response.get("code", "")
        raise ValidationError(f"Yingdao {endpoint} failed: {code} {message}".strip())
    data = response.get("data")
    if not isinstance(data, dict):
        raise ValidationError(f"Yingdao {endpoint} response did not include data object.")
    return data


def _int_from_env(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValidationError(f"{name} must be an integer.") from exc


def _to_yingdao_idempotent_uuid(value: str) -> str:
    normalized = value.strip()
    if len(normalized) <= 36:
        return normalized
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:36]


def _redact_yingdao_request_body(body: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(body)
    params = redacted.get("params")
    if isinstance(params, list):
        redacted["params"] = [
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "value_length": len(str(item.get("value") or "")),
            }
            if isinstance(item, dict)
            else item
            for item in params
        ]
    return redacted

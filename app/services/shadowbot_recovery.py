from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from app.enums import ReviewTaskStatus
from app.exceptions import ValidationError
from app.models import RetryAuthorization, ShadowBotExecutionAttempt
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.shadowbot_state import (
    AttemptStatus,
    OperationStatus,
    SideEffectState,
)
from app.utils import utc_now


AUTHORIZATION_ACTIVE = "ACTIVE"
AUTHORIZATION_AUTO_POLICY = "AUTO_POLICY"
AUTHORIZATION_MANUAL = "MANUAL"
EVIDENCE_NOT_APPLIED_RESULT = "NOT_APPLIED_RESULT"
EVIDENCE_PRE_PUBLISH_NOT_PUBLISHED = "PRE_PUBLISH_NOT_PUBLISHED"


def _as_retry_aware(value: datetime, reference: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=reference.tzinfo)
    return value


def _parse_retry_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    reference = utc_now()
    return _as_retry_aware(parsed, reference)


@dataclass(frozen=True, slots=True)
class RetryEvidence:
    evidence_type: str
    result_id: str
    instruction_hash: str
    request_file_sha256: str
    approved_payload_hash: str
    side_effect_state: str
    checksum_valid: bool
    ready_published: bool | None = None
    worker_claimed: bool | None = None
    platform_action: bool | None = None
    submit_intent_recorded: bool = False
    submit_clicked: bool = False
    unknown_checkpoint: bool = False
    failed_phase: str = ""
    cleanup_confirmed: bool = False
    cleanup_evidence: str = ""

    def canonical_payload(self, *, operation_id: str, source_execution_attempt_id: str) -> dict[str, Any]:
        return {
            "operation_id": operation_id,
            "source_execution_attempt_id": source_execution_attempt_id,
            "evidence_type": self.evidence_type,
            "result_id": self.result_id,
            "instruction_hash": self.instruction_hash,
            "request_file_sha256": self.request_file_sha256,
            "approved_payload_hash": self.approved_payload_hash,
            "side_effect_state": self.side_effect_state,
            "checksum_valid": self.checksum_valid,
            "ready_published": self.ready_published,
            "worker_claimed": self.worker_claimed,
            "platform_action": self.platform_action,
            "submit_intent_recorded": self.submit_intent_recorded,
            "submit_clicked": self.submit_clicked,
            "unknown_checkpoint": self.unknown_checkpoint,
            "failed_phase": self.failed_phase,
            "cleanup_confirmed": self.cleanup_confirmed,
            "cleanup_evidence": self.cleanup_evidence,
        }

    def evidence_hash(self, *, operation_id: str, source_execution_attempt_id: str) -> str:
        encoded = json.dumps(
            self.canonical_payload(
                operation_id=operation_id,
                source_execution_attempt_id=source_execution_attempt_id,
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class RetryPolicyService:
    """The only automatic signer for a new COMMIT attempt."""

    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        *,
        max_commit_attempts: int = 3,
        authorization_ttl: timedelta = timedelta(minutes=15),
        max_retry_window: timedelta = timedelta(hours=1),
    ) -> None:
        self.repository = repository
        self.max_commit_attempts = max_commit_attempts
        self.authorization_ttl = authorization_ttl
        self.max_retry_window = max_retry_window

    def issue_automatic(
        self,
        *,
        source_execution_attempt_id: str,
        evidence: RetryEvidence,
        now: datetime | None = None,
    ) -> RetryAuthorization:
        current = _as_retry_aware(now or utc_now(), utc_now())
        attempt, operation = self._load_source(source_execution_attempt_id)
        self._validate_common(attempt, operation, evidence, current)
        if evidence.evidence_type == EVIDENCE_NOT_APPLIED_RESULT:
            self._validate_not_applied(attempt, evidence)
        elif evidence.evidence_type == EVIDENCE_PRE_PUBLISH_NOT_PUBLISHED:
            self._validate_pre_publish(attempt, evidence)
        else:
            raise ValidationError("RETRY_AUTHORIZATION_EVIDENCE_UNSUPPORTED")
        authorization = self._authorization(
            attempt=attempt,
            evidence=evidence,
            authorization_type=AUTHORIZATION_AUTO_POLICY,
            authorized_by="RetryPolicyService",
            reason="automatic policy authorization from verified retry evidence",
            current=current,
        )
        if not self.repository.issue_retry_authorization(
            authorization,
            allowed_operation_statuses=(OperationStatus.FAILED.value, OperationStatus.NOT_APPLIED.value),
        ):
            raise ValidationError("RETRY_AUTHORIZATION_CONFLICT")
        return authorization

    def issue_manual(
        self,
        *,
        source_execution_attempt_id: str,
        actor: str,
        reason: str,
        evidence: RetryEvidence,
        now: datetime | None = None,
    ) -> RetryAuthorization:
        if not actor.strip() or not reason.strip():
            raise ValidationError("MANUAL_RETRY_AUTHORIZATION_REQUIRES_ACTOR_AND_REASON")
        current = _as_retry_aware(now or utc_now(), utc_now())
        attempt, operation = self._load_source(source_execution_attempt_id)
        self._validate_common(
            attempt,
            operation,
            evidence,
            current,
            allow_manual_review=True,
            allow_frozen_manual=True,
        )
        if evidence.evidence_type == EVIDENCE_NOT_APPLIED_RESULT:
            self._validate_not_applied(attempt, evidence, allow_frozen_manual=True)
        elif evidence.evidence_type == EVIDENCE_PRE_PUBLISH_NOT_PUBLISHED:
            self._validate_pre_publish(attempt, evidence, allow_frozen_manual=True)
        else:
            raise ValidationError("RETRY_AUTHORIZATION_EVIDENCE_UNSUPPORTED")
        authorization = self._authorization(
            attempt=attempt,
            evidence=evidence,
            authorization_type=AUTHORIZATION_MANUAL,
            authorized_by=actor.strip(),
            reason=reason.strip(),
            current=current,
        )
        if not self.repository.issue_retry_authorization(
            authorization,
            allowed_operation_statuses=(
                OperationStatus.FAILED.value,
                OperationStatus.NOT_APPLIED.value,
                OperationStatus.MANUAL_REVIEW.value,
            ),
        ):
            raise ValidationError("RETRY_AUTHORIZATION_CONFLICT")
        return authorization

    def _load_source(self, execution_attempt_id: str):
        attempt = self.repository.get_shadowbot_execution_attempt(execution_attempt_id)
        if attempt is None:
            raise ValidationError("RETRY_SOURCE_ATTEMPT_NOT_FOUND")
        operation = self.repository.get_shadowbot_operation(attempt.operation_id)
        if operation is None:
            raise ValidationError("RETRY_SOURCE_OPERATION_NOT_FOUND")
        return attempt, operation

    def _validate_common(
        self,
        attempt,
        operation,
        evidence: RetryEvidence,
        current: datetime,
        *,
        allow_manual_review: bool = False,
        allow_frozen_manual: bool = False,
    ) -> None:
        frozen_manual_source = bool(
            allow_frozen_manual
            and attempt.raw_output.get("frozen_reason") == "DUPLICATE_ACTIVE_COMMIT_ATTEMPT"
            and attempt.status in {
                AttemptStatus.START_UNKNOWN.value,
                AttemptStatus.SIDE_EFFECT_UNKNOWN.value,
            }
        )
        if attempt.execution_mode != "COMMIT" or attempt.status in {
            AttemptStatus.STARTING.value,
            AttemptStatus.RUNNING.value,
        } or (
            attempt.status in {
                AttemptStatus.START_UNKNOWN.value,
                AttemptStatus.SIDE_EFFECT_UNKNOWN.value,
            }
            and not frozen_manual_source
        ):
            raise ValidationError("RETRY_SOURCE_ATTEMPT_NOT_ELIGIBLE")
        blocked = {
            OperationStatus.VERIFIED.value,
            OperationStatus.NEEDS_RECONCILIATION.value,
            OperationStatus.MANUAL_HANDLED.value,
        }
        if not allow_manual_review:
            blocked.add(OperationStatus.MANUAL_REVIEW.value)
        if operation.status in blocked:
            raise ValidationError("RETRY_OPERATION_FROZEN")
        if evidence.approved_payload_hash != operation.approved_payload_hash:
            raise ValidationError("RETRY_APPROVED_PAYLOAD_MISMATCH")
        source_approval_id = str(attempt.raw_output.get("approval_id") or "").strip()
        source_approval_expiry = _parse_retry_timestamp(attempt.raw_output.get("approval_expires_at"))
        source_payload_hash = str(attempt.raw_output.get("approved_payload_hash") or "")
        if not source_approval_id or source_approval_expiry is None:
            raise ValidationError("RETRY_SOURCE_APPROVAL_BINDING_MISSING")
        if source_approval_expiry <= current:
            raise ValidationError("RETRY_SOURCE_APPROVAL_EXPIRED")
        if source_payload_hash != operation.approved_payload_hash:
            raise ValidationError("RETRY_SOURCE_APPROVAL_PAYLOAD_MISMATCH")
        review = self.repository.get_review_task(source_approval_id)
        if review is None or review.review_status != ReviewTaskStatus.APPROVED:
            raise ValidationError("RETRY_SOURCE_APPROVAL_NOT_APPROVED")
        stored_payload_hash = str(
            review.resolution_payload.get("approved_payload_hash")
            or review.review_payload.get("approved_payload_hash")
            or ""
        )
        if stored_payload_hash != source_payload_hash:
            raise ValidationError("RETRY_SOURCE_APPROVAL_PAYLOAD_MISMATCH")
        if not evidence.instruction_hash or evidence.instruction_hash != attempt.instruction_hash:
            raise ValidationError("RETRY_INSTRUCTION_HASH_MISMATCH")
        if evidence.evidence_type == EVIDENCE_NOT_APPLIED_RESULT:
            if not evidence.request_file_sha256 or evidence.request_file_sha256 != attempt.request_file_sha256:
                raise ValidationError("RETRY_REQUEST_HASH_MISMATCH")
        elif evidence.request_file_sha256 != attempt.request_file_sha256:
            raise ValidationError("RETRY_REQUEST_HASH_MISMATCH")
        if not evidence.result_id or not evidence.checksum_valid:
            raise ValidationError("RETRY_RESULT_EVIDENCE_INCOMPLETE")
        if evidence.submit_intent_recorded or evidence.submit_clicked or evidence.unknown_checkpoint:
            raise ValidationError("RETRY_SIDE_EFFECT_RISK_PRESENT")
        lease = attempt.raw_output.get("lease") if isinstance(attempt.raw_output.get("lease"), dict) else {}
        if bool(lease.get("active", False)) or operation.lock_owner:
            raise ValidationError("RETRY_SOURCE_LEASE_STILL_ACTIVE")
        attempts = self.repository.list_shadowbot_execution_attempts(operation_id=attempt.operation_id)
        commit_attempts = [item for item in attempts if item.execution_mode == "COMMIT"]
        if len(commit_attempts) >= self.max_commit_attempts:
            raise ValidationError("RETRY_BUDGET_EXHAUSTED")
        retry_origins = [item.started_at for item in commit_attempts]
        if operation.created_at is not None:
            retry_origins.append(operation.created_at)
        retry_origin = min(_as_retry_aware(value, current) for value in retry_origins)
        if current > retry_origin + self.max_retry_window:
            raise ValidationError("RETRY_TOTAL_TIME_WINDOW_EXPIRED")

    @staticmethod
    def _validate_not_applied(
        attempt,
        evidence: RetryEvidence,
        *,
        allow_frozen_manual: bool = False,
    ) -> None:
        frozen_manual_source = bool(
            allow_frozen_manual
            and attempt.raw_output.get("frozen_reason") == "DUPLICATE_ACTIVE_COMMIT_ATTEMPT"
            and attempt.status in {
                AttemptStatus.START_UNKNOWN.value,
                AttemptStatus.SIDE_EFFECT_UNKNOWN.value,
            }
        )
        if attempt.status not in {AttemptStatus.FAILED.value, AttemptStatus.NOT_APPLIED.value} and not frozen_manual_source:
            raise ValidationError("RETRY_NOT_APPLIED_SOURCE_STATUS_INVALID")
        if not frozen_manual_source and attempt.side_effect_state != SideEffectState.NOT_APPLIED.value:
            raise ValidationError("RETRY_NOT_APPLIED_PROOF_REQUIRED")
        if evidence.side_effect_state != SideEffectState.NOT_APPLIED.value:
            raise ValidationError("RETRY_NOT_APPLIED_PROOF_REQUIRED")
        if evidence.failed_phase == "TARGET_FILLED" and (
            not evidence.cleanup_confirmed or not evidence.cleanup_evidence
        ):
            raise ValidationError("RETRY_TARGET_CLEANUP_PROOF_REQUIRED")

    @staticmethod
    def _validate_pre_publish(
        attempt,
        evidence: RetryEvidence,
        *,
        allow_frozen_manual: bool = False,
    ) -> None:
        frozen_manual_source = bool(
            allow_frozen_manual
            and attempt.raw_output.get("frozen_reason") == "DUPLICATE_ACTIVE_COMMIT_ATTEMPT"
            and attempt.status == AttemptStatus.START_UNKNOWN.value
        )
        if attempt.status != AttemptStatus.START_FAILED.value and not frozen_manual_source:
            raise ValidationError("RETRY_PRE_PUBLISH_SOURCE_STATUS_INVALID")
        if attempt.side_effect_state != SideEffectState.NOT_STARTED.value:
            raise ValidationError("RETRY_PRE_PUBLISH_SIDE_EFFECT_INVALID")
        if evidence.ready_published is not False or evidence.worker_claimed is not False:
            raise ValidationError("RETRY_PRE_PUBLISH_PUBLICATION_PROOF_REQUIRED")
        if evidence.platform_action is not False:
            raise ValidationError("RETRY_PRE_PUBLISH_PLATFORM_PROOF_REQUIRED")

    def _authorization(
        self,
        *,
        attempt: ShadowBotExecutionAttempt,
        evidence: RetryEvidence,
        authorization_type: str,
        authorized_by: str,
        reason: str,
        current: datetime,
    ) -> RetryAuthorization:
        approval_expires_at = _parse_retry_timestamp(attempt.raw_output.get("approval_expires_at"))
        if approval_expires_at is None:
            raise ValidationError("RETRY_SOURCE_APPROVAL_BINDING_MISSING")
        return RetryAuthorization(
            retry_authorization_id=f"RETRY-{uuid4().hex}",
            operation_id=attempt.operation_id,
            source_execution_attempt_id=attempt.execution_attempt_id,
            authorization_type=authorization_type,
            authorized_by=authorized_by,
            evidence_type=evidence.evidence_type,
            evidence_hash=evidence.evidence_hash(
                operation_id=attempt.operation_id,
                source_execution_attempt_id=attempt.execution_attempt_id,
            ),
            approved_payload_hash=evidence.approved_payload_hash,
            status=AUTHORIZATION_ACTIVE,
            max_uses=1,
            expires_at=min(current + self.authorization_ttl, approval_expires_at),
            reason=reason,
            created_at=current,
        )


class ShadowBotLeaseWatchdog:
    """Fence expired leases and freeze duplicate active COMMIT attempts."""

    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository

    def inspect(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or utc_now()
        active = self.repository.list_active_shadowbot_execution_attempts()
        by_operation: dict[str, list[ShadowBotExecutionAttempt]] = {}
        for attempt in active:
            if attempt.execution_mode == "COMMIT":
                by_operation.setdefault(attempt.operation_id, []).append(attempt)
        events: list[dict[str, Any]] = []
        frozen: set[str] = set()
        for operation_id, attempts in by_operation.items():
            if len(attempts) > 1 and self.repository.freeze_duplicate_active_commit_attempts(
                operation_id, now=current
            ):
                frozen.add(operation_id)
                events.append(
                    {
                        "status": OperationStatus.MANUAL_REVIEW.value,
                        "error_code": "DUPLICATE_ACTIVE_COMMIT_ATTEMPT",
                        "operation_id": operation_id,
                        "execution_attempt_ids": [item.execution_attempt_id for item in attempts],
                    }
                )
        for attempt in active:
            if attempt.operation_id in frozen:
                continue
            if self.repository.expire_shadowbot_lease(attempt.execution_attempt_id, now=current):
                events.append(
                    {
                        "status": OperationStatus.NEEDS_RECONCILIATION.value,
                        "error_code": "LEASE_EXPIRED",
                        "operation_id": attempt.operation_id,
                        "execution_attempt_id": attempt.execution_attempt_id,
                    }
                )
        return events

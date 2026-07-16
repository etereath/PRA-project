"""Durable notification Outbox, delivery worker, and fake provider contracts.

The service in this module owns logical notification idempotency.  It never
performs a provider call while creating business state; provider calls happen
only after a worker has durably recorded a lease and STARTED attempt.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from typing import Protocol, Sequence
from uuid import uuid4

from app.enums import DeliveryClassification, NotificationOutboxStatus
from app.models import (
    NotificationDeliveryAttempt,
    NotificationDeliveryResult,
    NotificationLog,
    NotificationOutbox,
    ReviewTask,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.utils import utc_now


DEFAULT_NOTIFICATION_MAX_ATTEMPTS = 3
DEFAULT_NOTIFICATION_LEASE_SECONDS = 60
DEFAULT_NOTIFICATION_RETRY_SECONDS = 2
VERIFICATION_NOTIFICATION_TYPE = "verification_code_intervention"
REVIEW_TYPE_LABELS = {
    "capacity_warning": "产能预警",
    "labor_required": "人工用工确认",
    "shortage_warning": "短缺预警",
    "cold_storage_warning": "冷库预警",
    "clearance_warning": "清库存预警",
    "manual_price_review": "人工价格复核",
    "below_break_even_review": "低于保本价复核",
    "manual_review": "人工复核",
}


class NotificationSender(Protocol):
    """Provider adapter contract; it owns no SQLite transaction or retry loop."""

    channel: str

    def send(
        self,
        notification: NotificationOutbox,
        attempt: NotificationDeliveryAttempt,
    ) -> NotificationDeliveryResult:
        ...


class FakeSender:
    """A deterministic provider used by tests and local CI."""

    channel = "fake"

    def __init__(self, *, provider_message_id: str = "fake-message") -> None:
        self.provider_message_id = provider_message_id
        self.calls: list[tuple[str, int]] = []

    def send(
        self,
        notification: NotificationOutbox,
        attempt: NotificationDeliveryAttempt,
    ) -> NotificationDeliveryResult:
        self.calls.append((notification.notification_id, attempt.attempt_no))
        return NotificationDeliveryResult(
            classification=DeliveryClassification.SUCCESS.value,
            provider_status_code="200",
            provider_message_id=self.provider_message_id,
            response_fingerprint=_fingerprint({"provider": "fake", "status": 200}),
        )


class ScriptedSender:
    """Fake provider with the fault points required by the task 9 matrix."""

    channel = "scripted"

    def __init__(self, outcomes: Sequence[str] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[tuple[str, int]] = []

    def send(
        self,
        notification: NotificationOutbox,
        attempt: NotificationDeliveryAttempt,
    ) -> NotificationDeliveryResult:
        self.calls.append((notification.notification_id, attempt.attempt_no))
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        normalized = str(outcome).strip().lower()
        if normalized in {"success", "acknowledged", "provider_ack"}:
            return NotificationDeliveryResult(
                classification=DeliveryClassification.SUCCESS.value,
                provider_status_code="200",
                provider_message_id=f"scripted-{attempt.attempt_no}",
                response_fingerprint=_fingerprint({"outcome": normalized}),
            )
        if normalized in {"before_send", "temporary_reject", "temp_failed"}:
            return NotificationDeliveryResult(
                classification=DeliveryClassification.TEMP_FAILED.value,
                error_code="TEMPORARY_PROVIDER_FAILURE",
                error_message="scripted temporary failure before a proven send",
            )
        if normalized in {"permanent_reject", "perm_failed"}:
            return NotificationDeliveryResult(
                classification=DeliveryClassification.PERM_FAILED.value,
                provider_status_code="400",
                error_code="PERMANENT_PROVIDER_REJECTION",
                error_message="scripted permanent provider rejection",
            )
        if normalized in {
            "after_request_started",
            "after_provider_ack_before_db_write",
            "timeout_after_bytes_sent",
            "unknown",
        }:
            raise UnknownDelivery("scripted provider result is not provable")
        raise ValueError(f"unsupported scripted sender outcome: {outcome}")


class UnknownDelivery(RuntimeError):
    """Raised after a provider side effect may have happened."""


class NotificationOutboxService:
    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        *,
        clock=None,
        lease_seconds: int = DEFAULT_NOTIFICATION_LEASE_SECONDS,
    ) -> None:
        self.repository = repository
        self._clock = clock or utc_now
        self.lease_seconds = lease_seconds

    @staticmethod
    def notification_key(
        notification_type: str,
        entity_id: str,
        event_version: str,
        channel: str,
        recipient_ref: str,
    ) -> str:
        parts = [notification_type, entity_id, event_version, channel, recipient_ref]
        if any(not str(part).strip() for part in parts):
            raise ValueError("notification key components must not be empty")
        return ":".join(str(part).strip() for part in parts)

    def enqueue(
        self,
        *,
        notification_type: str,
        notification_key: str,
        recipient_type: str,
        recipient_ref: str,
        channel: str,
        payload: dict[str, object] | None = None,
        priority: int = 0,
        related_task_id: str | None = None,
        related_review_task_id: str | None = None,
        max_attempts: int = DEFAULT_NOTIFICATION_MAX_ATTEMPTS,
        deadline_at: datetime | None = None,
        next_attempt_at: datetime | None = None,
        notification_id: str | None = None,
    ) -> NotificationOutbox:
        if not notification_type.strip() or not notification_key.strip():
            raise ValueError("notification_type and notification_key are required")
        if not recipient_type.strip() or not recipient_ref.strip() or not channel.strip():
            raise ValueError("recipient_type, recipient_ref, and channel are required")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        normalized_payload = dict(payload or {})
        _reject_secrets(normalized_payload)
        now = self._clock()
        candidate = NotificationOutbox(
            notification_id=notification_id or uuid4().hex,
            notification_key=notification_key,
            notification_type=notification_type,
            recipient_type=recipient_type,
            recipient_ref=recipient_ref,
            channel=channel,
            priority=int(priority),
            payload=normalized_payload,
            related_task_id=related_task_id,
            related_review_task_id=related_review_task_id,
            status=NotificationOutboxStatus.PENDING.value,
            max_attempts=int(max_attempts),
            next_attempt_at=next_attempt_at,
            deadline_at=deadline_at,
            created_at=now,
            updated_at=now,
        )
        inserted = self.repository.insert_notification_outbox(candidate)
        if inserted == 1:
            self._ensure_compatibility_log(candidate)
            return candidate
        existing = self.repository.get_notification_outbox_by_key(notification_key)
        if existing is None:
            raise RuntimeError("notification insert was ignored but no existing key was found")
        self._ensure_compatibility_log(existing)
        return existing

    def enqueue_review_notification(
        self,
        review_task: ReviewTask,
        *,
        recipient_type: str = "role",
        recipient_ref: str = "operations",
        channel: str = "fake",
        event_version: str = "v1",
        priority: int = 50,
        max_attempts: int = DEFAULT_NOTIFICATION_MAX_ATTEMPTS,
        deadline_at: datetime | None = None,
    ) -> NotificationOutbox:
        key = self.notification_key(
            "mobile_review_required",
            review_task.review_task_id,
            event_version,
            channel,
            recipient_ref,
        )
        return self.enqueue(
            notification_type="mobile_review_required",
            notification_key=key,
            recipient_type=recipient_type,
            recipient_ref=recipient_ref,
            channel=channel,
            priority=priority,
            max_attempts=max_attempts,
            deadline_at=deadline_at or review_task.required_by,
            related_task_id=review_task.source_task_id,
            related_review_task_id=review_task.review_task_id,
            payload={
                "review_task_id": review_task.review_task_id,
                "review_type": review_task.review_type,
                "reason": review_task.reason[:500],
                "message": _review_notification_message(review_task),
                "scope_type": review_task.scope_type,
                "scope_key": review_task.scope_key,
            },
        )

    def enqueue_verification_intervention(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        recipient_type: str,
        recipient_ref: str,
        channel: str,
        payload: dict[str, object] | None = None,
        now: datetime | None = None,
        deadline_seconds: int = 300,
        max_attempts: int = 3,
    ) -> NotificationOutbox:
        if not 120 <= deadline_seconds <= 600:
            raise ValueError("verification notification deadline must be between 120 and 600 seconds")
        reference_time = now or self._clock()
        key = self.notification_key(
            VERIFICATION_NOTIFICATION_TYPE,
            operation_id,
            attempt_id,
            channel,
            recipient_ref,
        )
        safe_payload = {
            "operation_id": operation_id,
            "execution_attempt_id": attempt_id,
            "message": "人工介入已请求，请在系统中查看关联操作。",
        }
        if payload:
            safe_payload.update(payload)
        _reject_secrets(safe_payload)
        return self.enqueue(
            notification_type=VERIFICATION_NOTIFICATION_TYPE,
            notification_key=key,
            recipient_type=recipient_type,
            recipient_ref=recipient_ref,
            channel=channel,
            payload=safe_payload,
            priority=100,
            max_attempts=max_attempts,
            deadline_at=reference_time + timedelta(seconds=deadline_seconds),
        )

    def enqueue_review_task_atomically(
        self,
        review_task: ReviewTask,
        **kwargs,
    ) -> tuple[int, int, NotificationOutbox]:
        """Create a review task and its outbox intent in one SQLite commit."""

        candidate = self._candidate_review_notification(review_task, **kwargs)
        inserted_review, inserted_outbox = self.repository.insert_review_task_with_notification_outbox(
            review_task,
            candidate,
        )
        if inserted_review == 0:
            existing = self.repository.get_notification_outbox_by_key(candidate.notification_key)
            if existing is None:
                raise RuntimeError("duplicate review task has no matching notification outbox")
            return 0, 0, existing
        self._ensure_compatibility_log(candidate)
        return inserted_review, inserted_outbox, candidate

    def _ensure_compatibility_log(self, notification: NotificationOutbox) -> None:
        message_value = None
        if isinstance(notification.payload, dict):
            message_value = notification.payload.get("message") or notification.payload.get("reason")
        message = str(message_value or f"{notification.notification_type} queued")[:1000]
        self.repository.insert_notification_logs(
            [
                NotificationLog(
                    notification_id=notification.notification_id,
                    related_task_id=notification.related_task_id,
                    related_review_task_id=notification.related_review_task_id,
                    recipient_type=notification.recipient_type,
                    recipient=notification.recipient_ref,
                    channel=notification.channel,
                    sent_at=notification.sent_at,
                    send_status="pending",
                    dedupe_key=notification.notification_key,
                    message=message,
                    created_at=notification.created_at,
                )
            ]
        )

    def _sync_compatibility_log(self, notification: NotificationOutbox) -> None:
        if notification.status == NotificationOutboxStatus.SENT.value:
            status = "success"
            sent_at = notification.sent_at
        elif notification.status in {
            NotificationOutboxStatus.PENDING.value,
            NotificationOutboxStatus.LEASED.value,
            NotificationOutboxStatus.SENDING.value,
            NotificationOutboxStatus.RETRY_WAIT.value,
        }:
            status = "pending"
            sent_at = None
        else:
            status = "failed"
            sent_at = None
        self.repository.update_notification_log_delivery(
            notification.notification_id,
            send_status=status,
            sent_at=sent_at,
            error_message=notification.last_error_message,
        )

    def _candidate_review_notification(self, review_task: ReviewTask, **kwargs) -> NotificationOutbox:
        # Build without writing, so the caller can pass the candidate into the
        # repository's single-transaction insert method.
        recipient_type = kwargs.get("recipient_type", "role")
        recipient_ref = kwargs.get("recipient_ref", "operations")
        channel = kwargs.get("channel", "fake")
        event_version = kwargs.get("event_version", "v1")
        priority = kwargs.get("priority", 50)
        max_attempts = kwargs.get("max_attempts", DEFAULT_NOTIFICATION_MAX_ATTEMPTS)
        deadline_at = kwargs.get("deadline_at") or review_task.required_by
        key = self.notification_key(
            "mobile_review_required", review_task.review_task_id, event_version, channel, recipient_ref
        )
        now = self._clock()
        return NotificationOutbox(
            notification_id=uuid4().hex,
            notification_key=key,
            notification_type="mobile_review_required",
            related_task_id=review_task.source_task_id,
            related_review_task_id=review_task.review_task_id,
            recipient_type=recipient_type,
            recipient_ref=recipient_ref,
            channel=channel,
            priority=int(priority),
            payload={
                "review_task_id": review_task.review_task_id,
                "review_type": review_task.review_type,
                "reason": review_task.reason[:500],
                "message": _review_notification_message(review_task),
                "scope_type": review_task.scope_type,
                "scope_key": review_task.scope_key,
            },
            status=NotificationOutboxStatus.PENDING.value,
            max_attempts=int(max_attempts),
            deadline_at=deadline_at,
            created_at=now,
            updated_at=now,
        )

    def deliver_once(
        self,
        sender: NotificationSender,
        *,
        now: datetime | None = None,
        lease_seconds: int | None = None,
    ) -> NotificationOutbox | None:
        reference_time = now or self._clock()
        claimed = self.repository.claim_notification_outbox(
            now=reference_time,
            lease_seconds=lease_seconds or self.lease_seconds,
            limit=1,
        )
        if not claimed:
            return None
        notification = claimed[0]
        attempt = self.repository.begin_notification_delivery(
            notification.notification_id,
            owner_token=notification.lease_owner_token,
            lease_version=notification.lease_version,
            request_fingerprint=_fingerprint(notification.payload),
            now=reference_time,
        )
        try:
            result = sender.send(notification, attempt)
        except UnknownDelivery:
            result = NotificationDeliveryResult(
                classification=DeliveryClassification.UNKNOWN.value,
                error_code="UNKNOWN_DELIVERY",
                error_message="provider result was not provable",
            )
        except (TimeoutError, ConnectionError, OSError) as exc:
            result = NotificationDeliveryResult(
                classification=DeliveryClassification.UNKNOWN.value,
                error_code="UNKNOWN_DELIVERY",
                error_message=f"provider call was interrupted: {type(exc).__name__}",
            )
        except Exception as exc:
            # Once a sender has been called, retrying an unclassified exception
            # could duplicate an external side effect; fence it as unknown.
            result = NotificationDeliveryResult(
                classification=DeliveryClassification.UNKNOWN.value,
                error_code="UNKNOWN_DELIVERY",
                error_message=f"provider result was not provable: {type(exc).__name__}",
            )
        final = self.repository.complete_notification_delivery(
            notification.notification_id,
            attempt.delivery_attempt_id,
            owner_token=notification.lease_owner_token,
            lease_version=notification.lease_version,
            result=result,
            now=reference_time,
        )
        self._sync_compatibility_log(final)
        return final

    def watchdog(self, *, now: datetime | None = None) -> list[NotificationOutbox]:
        return self.repository.recover_expired_notification_leases(now=now or self._clock())


class NotificationOutboxWorker:
    """Small worker facade suitable for a scheduler or a test loop."""

    def __init__(self, service: NotificationOutboxService, sender: NotificationSender) -> None:
        self.service = service
        self.sender = sender

    def run_once(self, *, now: datetime | None = None) -> NotificationOutbox | None:
        return self.service.deliver_once(self.sender, now=now)

    def run_watchdog(self, *, now: datetime | None = None) -> list[NotificationOutbox]:
        return self.service.watchdog(now=now)


OutboxService = NotificationOutboxService
NotificationWorker = NotificationOutboxWorker


class OutboxReviewNotificationService:
    """Review-facing adapter that creates intents, never provider calls."""

    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository
        self.outbox_service = NotificationOutboxService(repository)

    def create_review_task_atomically(self, review_task: ReviewTask) -> tuple[int, int, NotificationOutbox]:
        channel = os.getenv("DEFAULT_NOTIFICATION_CHANNEL", "mock").strip() or "mock"
        result = self.outbox_service.enqueue_review_task_atomically(
            review_task,
            recipient_type=os.getenv("DEFAULT_NOTIFICATION_RECIPIENT_TYPE", "role").strip() or "role",
            recipient_ref=os.getenv("DEFAULT_NOTIFICATION_RECIPIENT", "operations").strip() or "operations",
            channel=channel,
        )
        # The mock channel is intentionally local and deterministic.  Treat it
        # as a worker tick so existing local smoke flows still observe the
        # compatibility log reaching success; real channels remain queued.
        if result[1] == 1 and channel == "mock":
            self.outbox_service.deliver_once(FakeSender(), now=self.outbox_service._clock())
        final = self.repository.get_notification_outbox(result[2].notification_id) or result[2]
        return result[0], result[1], final

    def create_initial_notification(self, review_task: ReviewTask) -> NotificationLog | None:
        outbox = self.outbox_service.enqueue_review_notification(
            review_task,
            recipient_type=os.getenv("DEFAULT_NOTIFICATION_RECIPIENT_TYPE", "role").strip() or "role",
            recipient_ref=os.getenv("DEFAULT_NOTIFICATION_RECIPIENT", "operations").strip() or "operations",
            channel=os.getenv("DEFAULT_NOTIFICATION_CHANNEL", "mock").strip() or "mock",
        )
        return self.repository.get_notification_log(outbox.notification_id)

    def create_expired_notification(self, review_task: ReviewTask, *, timeout_at: datetime) -> NotificationLog | None:
        channel = os.getenv("DEFAULT_NOTIFICATION_CHANNEL", "mock").strip() or "mock"
        recipient_type = os.getenv("DEFAULT_NOTIFICATION_RECIPIENT_TYPE", "role").strip() or "role"
        recipient_ref = os.getenv("DEFAULT_NOTIFICATION_RECIPIENT", "operations").strip() or "operations"
        key = self.outbox_service.notification_key(
            "review_expired", review_task.review_task_id, "v1", channel, recipient_ref
        )
        outbox = self.outbox_service.enqueue(
            notification_type="review_expired",
            notification_key=key,
            recipient_type=recipient_type,
            recipient_ref=recipient_ref,
            channel=channel,
            related_task_id=review_task.source_task_id,
            related_review_task_id=review_task.review_task_id,
            payload={
                "review_task_id": review_task.review_task_id,
                "message": f"人工复核已过期：{timeout_at.isoformat()}",
            },
            priority=70,
            max_attempts=2,
            deadline_at=timeout_at + timedelta(minutes=5),
        )
        if channel == "mock":
            self.outbox_service.deliver_once(FakeSender(), now=self.outbox_service._clock())
        return self.repository.get_notification_log(outbox.notification_id)


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _review_notification_message(review_task: ReviewTask) -> str:
    label = REVIEW_TYPE_LABELS.get(review_task.review_type, review_task.review_type)
    reason = review_task.reason.strip()
    return f"{label}：{reason}" if reason else label


def _reject_secrets(value: object, *, _path: str = "payload") -> None:
    forbidden = {"token", "access_token", "authorization", "cookie", "password", "secret", "webhook_url"}
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized in forbidden or any(word in normalized for word in ("token", "password", "secret")):
                raise ValueError(f"notification payload contains forbidden secret field: {_path}.{key}")
            _reject_secrets(nested, _path=f"{_path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_secrets(nested, _path=f"{_path}[{index}]")

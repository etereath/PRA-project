"""Durable notification Outbox, delivery worker, and fake provider contracts.

The service in this module owns logical notification idempotency.  It never
performs a provider call while creating business state; provider calls happen
only after a worker has durably recorded a lease and STARTED attempt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from app.enums import DeliveryClassification, NotificationOutboxStatus
from app.exceptions import (
    NotificationChannelMismatchError,
    NotificationDeliveryError,
    NotificationIdempotencyConflictError,
)
from app.services.feishu import (
    build_feishu_signature,
    has_feishu_confirmation_code,
    is_feishu_success_response,
)
from app.models import (
    NotificationDeliveryAttempt,
    NotificationDeliveryResult,
    NotificationLog,
    NotificationOutbox,
    ReviewTask,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.review_policy import (
    is_execution_failure_review,
    review_task_group_id,
)
from app.utils import utc_now


DEFAULT_NOTIFICATION_MAX_ATTEMPTS = 3
DEFAULT_NOTIFICATION_LEASE_SECONDS = 60
DEFAULT_NOTIFICATION_RETRY_SECONDS = 2
BEIJING_TIMEZONE = timezone(timedelta(hours=8))
VERIFICATION_NOTIFICATION_TYPE = "verification_code_intervention"
NOTIFICATION_KEY_VERSION = "v1"
MAX_NOTIFICATION_PAYLOAD_BYTES = 16_384
TEST_NOTIFICATION_CHANNELS = frozenset({"mock", "fake", "scripted"})
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


def is_test_notification_channel(channel: object) -> bool:
    return _normalize_channel(channel) in TEST_NOTIFICATION_CHANNELS


class FakeSender:
    """A deterministic provider used by tests and local CI."""

    channel = "fake"

    def __init__(self, *, provider_message_id: str = "fake-message", channel: str = "fake") -> None:
        self.provider_message_id = provider_message_id
        self.channel = channel.strip().lower() or "fake"
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


class FeishuOutboxSender:
    """Feishu webhook adapter implementing the v6 Outbox sender contract.

    The adapter deliberately stores only bounded status/error metadata.  It
    never persists the webhook URL, response body, or authorization material.
    """

    channel = "feishu"

    def send(
        self,
        notification: NotificationOutbox,
        attempt: NotificationDeliveryAttempt,
    ) -> NotificationDeliveryResult:
        webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        if not webhook_url:
            return NotificationDeliveryResult(
                classification=DeliveryClassification.PERM_FAILED.value,
                error_code="FEISHU_WEBHOOK_URL_REQUIRED",
                error_message="Feishu channel is not configured",
            )
        message_type = os.getenv("FEISHU_MESSAGE_TYPE", "text").strip().lower() or "text"
        if message_type not in {"text", "post"}:
            return NotificationDeliveryResult(
                classification=DeliveryClassification.PERM_FAILED.value,
                error_code="FEISHU_MESSAGE_TYPE_INVALID",
                error_message="Feishu message type is invalid",
            )
        body = _build_feishu_outbox_body(notification, message_type)
        secret = os.getenv("FEISHU_WEBHOOK_SECRET", "").strip()
        if secret:
            timestamp = str(int(time.time()))
            body["timestamp"] = timestamp
            body["sign"] = build_feishu_signature(timestamp, secret)
        request = Request(
            webhook_url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=_feishu_timeout_seconds()) as response:
                status_code = int(response.getcode())
                response_text = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            # HTTPError may contain credentials or an echoed webhook URL.
            if exc.code == 429:
                classification = DeliveryClassification.TEMP_FAILED.value
            elif exc.code >= 500:
                classification = DeliveryClassification.UNKNOWN.value
            else:
                classification = DeliveryClassification.PERM_FAILED.value
            return NotificationDeliveryResult(
                classification=classification,
                provider_status_code=str(exc.code),
                error_code=(
                    "FEISHU_HTTP_TEMP_FAILED"
                    if classification == DeliveryClassification.TEMP_FAILED.value
                    else "FEISHU_UNKNOWN_DELIVERY"
                    if classification == DeliveryClassification.UNKNOWN.value
                    else "FEISHU_HTTP_REJECTED"
                ),
                error_message=f"Feishu provider HTTP {exc.code}",
            )
        except (TimeoutError, URLError, OSError) as exc:
            return NotificationDeliveryResult(
                classification=DeliveryClassification.UNKNOWN.value,
                error_code="FEISHU_UNKNOWN_DELIVERY",
                error_message=f"Feishu provider result was not provable: {type(exc).__name__}",
            )

        summary = _feishu_response_summary(status_code, response_text)
        provider_message_id = str(summary.get("request_id") or summary.get("RequestId") or "")[:200]
        response_fingerprint = _fingerprint(summary)
        if not summary.get("valid_json", False):
            return NotificationDeliveryResult(
                classification=DeliveryClassification.UNKNOWN.value,
                provider_status_code=str(status_code),
                provider_message_id=provider_message_id,
                response_fingerprint=response_fingerprint,
                error_code="FEISHU_UNKNOWN_DELIVERY",
                error_message="Feishu provider response was not provable",
            )
        if status_code == 429:
            return NotificationDeliveryResult(
                classification=DeliveryClassification.TEMP_FAILED.value,
                provider_status_code=str(status_code),
                provider_message_id=provider_message_id,
                response_fingerprint=response_fingerprint,
                error_code="FEISHU_HTTP_TEMP_FAILED",
                error_message=f"Feishu provider HTTP {status_code}",
            )
        if status_code >= 500:
            return NotificationDeliveryResult(
                classification=DeliveryClassification.UNKNOWN.value,
                provider_status_code=str(status_code),
                provider_message_id=provider_message_id,
                response_fingerprint=response_fingerprint,
                error_code="FEISHU_UNKNOWN_DELIVERY",
                error_message=f"Feishu provider HTTP {status_code}",
            )
        if status_code < 200 or status_code >= 300:
            return NotificationDeliveryResult(
                classification=DeliveryClassification.PERM_FAILED.value,
                provider_status_code=str(status_code),
                provider_message_id=provider_message_id,
                response_fingerprint=response_fingerprint,
                error_code="FEISHU_HTTP_REJECTED",
                error_message=f"Feishu provider HTTP {status_code}",
            )
        if not has_feishu_confirmation_code(summary):
            return NotificationDeliveryResult(
                classification=DeliveryClassification.UNKNOWN.value,
                provider_status_code=str(status_code),
                provider_message_id=provider_message_id,
                response_fingerprint=response_fingerprint,
                error_code="FEISHU_CONFIRMATION_MISSING",
                error_message="Feishu provider response did not contain an explicit confirmation code",
            )
        if not is_feishu_success_response(summary):
            code = summary.get("code", summary.get("StatusCode", ""))
            return NotificationDeliveryResult(
                classification=DeliveryClassification.PERM_FAILED.value,
                provider_status_code=str(code)[:100],
                provider_message_id=provider_message_id,
                response_fingerprint=response_fingerprint,
                error_code="FEISHU_PROVIDER_REJECTED",
                error_message="Feishu provider rejected the notification",
            )
        return NotificationDeliveryResult(
            classification=DeliveryClassification.SUCCESS.value,
            provider_status_code=str(status_code),
            provider_message_id=provider_message_id,
            response_fingerprint=response_fingerprint,
        )


class NotificationChannelRegistry:
    """Select an Outbox sender from the persisted channel name."""

    def __init__(self, builders: dict[str, Callable[[], NotificationSender]] | None = None) -> None:
        self._builders = dict(builders or {})
        self._builders.setdefault("fake", lambda: FakeSender(channel="fake"))
        self._builders.setdefault("mock", lambda: FakeSender(channel="mock"))
        self._builders.setdefault("scripted", lambda: ScriptedSender())
        self._builders.setdefault("feishu", FeishuOutboxSender)

    def build(self, channel: str) -> NotificationSender:
        normalized = str(channel or "").strip().lower()
        builder = self._builders.get(normalized)
        if builder is None:
            raise NotificationDeliveryError(f"unsupported notification channel: {normalized or '-'}")
        sender = builder()
        if str(getattr(sender, "channel", "")).strip().lower() != normalized:
            raise NotificationChannelMismatchError(
                f"notification channel registry returned an adapter for {normalized!r} with a different channel"
            )
        return sender


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
        channel = _normalize_channel(channel)
        parts = [notification_type, entity_id, event_version, channel, recipient_ref]
        if any(not str(part).strip() for part in parts):
            raise ValueError("notification key components must not be empty")
        canonical = {
            "channel": str(channel).strip(),
            "entity_id": str(entity_id).strip(),
            "event_version": str(event_version).strip(),
            "notification_type": str(notification_type).strip(),
            "recipient_ref": str(recipient_ref).strip(),
        }
        return f"{NOTIFICATION_KEY_VERSION}:{_fingerprint(canonical)}"

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
        notification_type = str(notification_type or "").strip()
        notification_key = str(notification_key or "").strip()
        recipient_type = str(recipient_type or "").strip()
        recipient_ref = str(recipient_ref or "").strip()
        channel = _normalize_channel(channel)
        if not notification_type or not notification_key:
            raise ValueError("notification_type and notification_key are required")
        if not recipient_type or not recipient_ref or not channel:
            raise ValueError("recipient_type, recipient_ref, and channel are required")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        normalized_payload = _sanitize_payload(notification_type, payload or {})
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
        _assert_idempotent_notification_match(candidate, existing)
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
            "message": "ShadowBot 登录验证码人工接管：请在系统中查看关联操作。",
        }
        if payload:
            allowed_overrides = {"platform_name", "required_by", "verification_markers"}
            unexpected = set(payload) - allowed_overrides
            if unexpected:
                raise ValueError(
                    "verification notification payload fields are not allowed: "
                    + ", ".join(sorted(str(key) for key in unexpected))
                )
            safe_payload.update(payload)
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

    def enqueue_verification_review_task_atomically(
        self,
        review_task: ReviewTask,
        *,
        operation_id: str,
        attempt_id: str,
        recipient_type: str,
        recipient_ref: str,
        channel: str,
        payload: dict[str, object] | None = None,
        max_attempts: int = 3,
    ) -> tuple[int, int, NotificationOutbox]:
        """Atomically create the ShadowBot review and verification Outbox intent."""

        candidate = self._candidate_verification_notification(
            review_task,
            operation_id=operation_id,
            attempt_id=attempt_id,
            recipient_type=recipient_type,
            recipient_ref=recipient_ref,
            channel=channel,
            payload=payload,
            max_attempts=max_attempts,
        )
        inserted_review, inserted_outbox = self.repository.insert_review_task_with_notification_outbox(
            review_task,
            candidate,
            compatibility_log=self._compatibility_log(candidate),
        )
        if inserted_review == 0:
            existing = self.repository.get_notification_outbox_by_key(candidate.notification_key)
            if existing is None:
                raise RuntimeError("duplicate verification review has no matching notification outbox")
            _assert_idempotent_notification_match(candidate, existing)
            self._ensure_compatibility_log(existing)
            return 0, 0, existing
        return inserted_review, inserted_outbox, candidate

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
            compatibility_log=self._compatibility_log(candidate),
        )
        if inserted_review == 0:
            existing_review = self.repository.get_pending_review_task_by_dedupe_key(review_task.dedupe_key)
            if existing_review is None:
                raise RuntimeError("duplicate review task could not be resolved by dedupe_key")
            existing_rows = self.repository.list_notification_outbox(
                related_review_task_id=existing_review.review_task_id
            )
            existing = next(
                (row for row in existing_rows if row.notification_type == "mobile_review_required"),
                None,
            )
            if existing is None:
                raise RuntimeError("duplicate review task has no matching notification outbox")
            _assert_duplicate_review_match(review_task, existing_review, candidate, existing)
            existing_candidate = self._candidate_review_notification(existing_review, **kwargs)
            _assert_idempotent_notification_match(existing_candidate, existing)
            self._ensure_compatibility_log(existing)
            return 0, 0, existing
        return inserted_review, inserted_outbox, candidate

    def _compatibility_log(self, notification: NotificationOutbox) -> NotificationLog:
        message_value = None
        if isinstance(notification.payload, dict):
            message_value = notification.payload.get("message") or notification.payload.get("reason")
        message = str(message_value or f"{notification.notification_type} queued")[:1000]
        return NotificationLog(
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

    def build_expired_notification_candidate(
        self,
        review_task: ReviewTask,
        *,
        timeout_at: datetime,
    ) -> tuple[NotificationOutbox, NotificationLog]:
        channel = _configured_notification_channel()
        recipient_type = os.getenv("DEFAULT_NOTIFICATION_RECIPIENT_TYPE", "role").strip() or "role"
        recipient_ref = os.getenv("DEFAULT_NOTIFICATION_RECIPIENT", "operations").strip() or "operations"
        candidate = self._candidate_expired_notification(
            review_task,
            timeout_at=timeout_at,
            recipient_type=recipient_type,
            recipient_ref=recipient_ref,
            channel=channel,
        )
        return candidate, self._compatibility_log(candidate)

    def build_review_notification_candidate(
        self,
        review_task: ReviewTask,
        *,
        event_version: str,
    ) -> tuple[NotificationOutbox, NotificationLog]:
        candidate = self._candidate_review_notification(
            review_task,
            recipient_type=os.getenv(
                "DEFAULT_NOTIFICATION_RECIPIENT_TYPE",
                "role",
            ).strip()
            or "role",
            recipient_ref=os.getenv(
                "DEFAULT_NOTIFICATION_RECIPIENT",
                "operations",
            ).strip()
            or "operations",
            channel=_configured_notification_channel(),
            event_version=event_version,
        )
        return candidate, self._compatibility_log(candidate)

    def _ensure_compatibility_log(self, notification: NotificationOutbox) -> None:
        self.repository.insert_notification_logs([self._compatibility_log(notification)])

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
        channel = _normalize_channel(kwargs.get("channel", "fake"))
        event_version = kwargs.get("event_version", "v1")
        priority = kwargs.get("priority", 50)
        max_attempts = kwargs.get("max_attempts", DEFAULT_NOTIFICATION_MAX_ATTEMPTS)
        deadline_at = kwargs.get("deadline_at") or review_task.required_by
        key = self.notification_key(
            "mobile_review_required", review_task.review_task_id, event_version, channel, recipient_ref
        )
        now = self._clock()
        payload = _sanitize_payload(
            "mobile_review_required",
            {
                "review_task_id": review_task.review_task_id,
                "review_type": review_task.review_type,
                "reason": review_task.reason[:500],
                "message": _review_notification_message(review_task),
                "scope_type": review_task.scope_type,
                "scope_key": review_task.scope_key,
            },
        )
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
            payload=payload,
            status=NotificationOutboxStatus.PENDING.value,
            max_attempts=int(max_attempts),
            deadline_at=deadline_at,
            created_at=now,
            updated_at=now,
        )

    def _candidate_expired_notification(
        self,
        review_task: ReviewTask,
        *,
        timeout_at: datetime,
        recipient_type: str,
        recipient_ref: str,
        channel: str,
    ) -> NotificationOutbox:
        channel = _normalize_channel(channel)
        now = self._clock()
        return NotificationOutbox(
            notification_id=uuid4().hex,
            notification_key=self.notification_key(
                "review_expired", review_task.review_task_id, "v1", channel, recipient_ref
            ),
            notification_type="review_expired",
            related_task_id=review_task.source_task_id,
            related_review_task_id=review_task.review_task_id,
            recipient_type=recipient_type,
            recipient_ref=recipient_ref,
            channel=channel,
            priority=70,
            payload=_sanitize_payload(
                "review_expired",
                {
                    "review_task_id": review_task.review_task_id,
                    "message": f"人工复核已过期：{timeout_at.isoformat()}",
                },
            ),
            status=NotificationOutboxStatus.PENDING.value,
            max_attempts=2,
            deadline_at=timeout_at + timedelta(minutes=5),
            created_at=now,
            updated_at=now,
        )

    def _candidate_verification_notification(
        self,
        review_task: ReviewTask,
        *,
        operation_id: str,
        attempt_id: str,
        recipient_type: str,
        recipient_ref: str,
        channel: str,
        payload: dict[str, object] | None,
        max_attempts: int,
    ) -> NotificationOutbox:
        if review_task.required_by is None:
            raise ValueError("verification review task must have a deadline")
        now = self._clock()
        comparable_deadline = _coerce_datetime_for_comparison(review_task.required_by, now)
        ttl_seconds = (comparable_deadline - now).total_seconds()
        if not 120 <= ttl_seconds <= 600:
            raise ValueError("verification review deadline must be between 120 and 600 seconds")
        safe_payload = {
            "operation_id": operation_id,
            "execution_attempt_id": attempt_id,
            "message": "ShadowBot 登录验证码人工接管：请在系统中查看关联操作。",
        }
        if payload:
            allowed_overrides = {"platform_name", "required_by", "verification_markers"}
            unexpected = set(payload) - allowed_overrides
            if unexpected:
                raise ValueError(
                    "verification notification payload fields are not allowed: "
                    + ", ".join(sorted(str(key) for key in unexpected))
                )
            safe_payload.update(payload)
        normalized_payload = _sanitize_payload(VERIFICATION_NOTIFICATION_TYPE, safe_payload)
        return NotificationOutbox(
            notification_id=uuid4().hex,
            notification_key=self.notification_key(
                VERIFICATION_NOTIFICATION_TYPE,
                operation_id,
                attempt_id,
                channel,
                recipient_ref,
            ),
            notification_type=VERIFICATION_NOTIFICATION_TYPE,
            related_task_id=review_task.source_task_id,
            related_review_task_id=review_task.review_task_id,
            recipient_type=recipient_type,
            recipient_ref=recipient_ref,
            channel=_normalize_channel(channel),
            priority=100,
            payload=normalized_payload,
            status=NotificationOutboxStatus.PENDING.value,
            max_attempts=max_attempts,
            deadline_at=review_task.required_by,
            created_at=now,
            updated_at=now,
        )

    def deliver_once(
        self,
        sender: NotificationSender,
        *,
        now: datetime | None = None,
        lease_seconds: int | None = None,
        channel: str | None = None,
    ) -> NotificationOutbox | None:
        actual_sender_channel = str(getattr(sender, "channel", "")).strip().lower()
        sender_channel = str(channel or actual_sender_channel).strip().lower()
        if not sender_channel or actual_sender_channel != sender_channel:
            raise NotificationChannelMismatchError("notification sender has no channel")
        # Each repository operation obtains its own time after acquiring the
        # write lock.  Never carry one timestamp across the provider call.
        claimed = self.repository.claim_notification_outbox(
            lease_seconds=lease_seconds or self.lease_seconds,
            limit=1,
            channel=sender_channel,
        )
        if not claimed:
            return None
        notification = claimed[0]
        if notification.channel.strip().lower() != sender_channel:
            raise NotificationChannelMismatchError(
                f"persisted channel {notification.channel!r} does not match sender channel {sender_channel!r}"
            )
        attempt = self.repository.begin_notification_delivery(
            notification.notification_id,
            owner_token=notification.lease_owner_token,
            lease_version=notification.lease_version,
            request_fingerprint=_fingerprint(notification.payload),
        )
        delivery_notification = notification
        review_token_id = ""
        try:
            delivery_notification, review_token_id = (
                self._prepare_delivery_notification(notification, sender)
            )
        except Exception as exc:
            result = NotificationDeliveryResult(
                classification=DeliveryClassification.PERM_FAILED.value,
                error_code="MOBILE_REVIEW_URL_CREATION_FAILED",
                error_message=(
                    "mobile review URL could not be created: "
                    + type(exc).__name__
                ),
            )
        else:
            try:
                result = sender.send(delivery_notification, attempt)
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
        if (
            review_token_id
            and result.classification
            in {
                DeliveryClassification.TEMP_FAILED.value,
                DeliveryClassification.PERM_FAILED.value,
            }
        ):
            self._revoke_unused_review_token(review_token_id)
        final = self.repository.complete_notification_delivery(
            notification.notification_id,
            attempt.delivery_attempt_id,
            owner_token=notification.lease_owner_token,
            lease_version=notification.lease_version,
            result=result,
        )
        return final

    def _prepare_delivery_notification(
        self,
        notification: NotificationOutbox,
        sender: NotificationSender,
    ) -> tuple[NotificationOutbox, str]:
        if (
            not isinstance(sender, FeishuOutboxSender)
            or notification.channel != "feishu"
            or notification.notification_type != "mobile_review_required"
            or not notification.related_review_task_id
        ):
            return notification, ""
        from app.services.runtime import ReviewTokenService

        token = ReviewTokenService(self.repository).create_token(
            notification.related_review_task_id,
            created_by="notification_outbox_worker",
            note="ephemeral Feishu mobile review link",
        )
        payload = dict(notification.payload)
        payload["mobile_review_url"] = token.mobile_review_url
        return replace(notification, payload=payload), token.review_token.token_id

    def _revoke_unused_review_token(self, review_token_id: str) -> None:
        from app.services.runtime import ReviewTokenService

        try:
            ReviewTokenService(self.repository).revoke_token(review_token_id)
        except Exception:
            # Delivery state remains authoritative. Token cleanup is best effort
            # and must never replace a proven provider result.
            return

    def watchdog(self, *, now: datetime | None = None) -> list[NotificationOutbox]:
        return self.repository.recover_expired_notification_leases()


class NotificationOutboxWorker:
    """Small worker facade suitable for a scheduler or a test loop."""

    def __init__(self, service: NotificationOutboxService, sender: NotificationSender) -> None:
        self.service = service
        self.sender = sender

    @classmethod
    def for_channel(
        cls,
        repository: SQLiteRuntimeRepository,
        channel: str,
        *,
        registry: NotificationChannelRegistry | None = None,
        lease_seconds: int = DEFAULT_NOTIFICATION_LEASE_SECONDS,
        allow_test_channels: bool = False,
    ) -> "NotificationOutboxWorker":
        normalized_channel = _normalize_channel(channel)
        if is_test_notification_channel(normalized_channel) and not allow_test_channels:
            raise NotificationDeliveryError(
                "test notification channels require an explicitly enabled test worker"
            )
        selected_registry = registry or NotificationChannelRegistry()
        return cls(
            NotificationOutboxService(repository, lease_seconds=lease_seconds),
            selected_registry.build(normalized_channel),
        )

    def run_once(self, *, now: datetime | None = None) -> NotificationOutbox | None:
        return self.service.deliver_once(self.sender, now=now, channel=self.sender.channel)

    def run_watchdog(self, *, now: datetime | None = None) -> list[NotificationOutbox]:
        return self.service.watchdog(now=now)


OutboxService = NotificationOutboxService
NotificationWorker = NotificationOutboxWorker


class OutboxReviewNotificationService:
    """Review-facing adapter that creates intents, never provider calls."""

    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository
        self.outbox_service = NotificationOutboxService(repository)
        self.channel_registry = NotificationChannelRegistry()

    def create_review_task_atomically(self, review_task: ReviewTask) -> tuple[int, int, NotificationOutbox]:
        channel = _configured_notification_channel()
        result = self.outbox_service.enqueue_review_task_atomically(
            review_task,
            recipient_type=os.getenv("DEFAULT_NOTIFICATION_RECIPIENT_TYPE", "role").strip() or "role",
            recipient_ref=os.getenv("DEFAULT_NOTIFICATION_RECIPIENT", "operations").strip() or "operations",
            channel=channel,
        )
        return result

    def create_verification_review_task_atomically(
        self,
        review_task: ReviewTask,
        *,
        operation_id: str,
        attempt_id: str,
        payload: dict[str, object] | None = None,
    ) -> tuple[int, int, NotificationOutbox]:
        channel = _configured_notification_channel()
        result = self.outbox_service.enqueue_verification_review_task_atomically(
            review_task,
            operation_id=operation_id,
            attempt_id=attempt_id,
            recipient_type=os.getenv("DEFAULT_NOTIFICATION_RECIPIENT_TYPE", "role").strip() or "role",
            recipient_ref=os.getenv("DEFAULT_NOTIFICATION_RECIPIENT", "operations").strip() or "operations",
            channel=channel,
            payload=payload,
        )
        return result

    def create_initial_notification(self, review_task: ReviewTask) -> NotificationLog | None:
        outbox = self.outbox_service.enqueue_review_notification(
            review_task,
            recipient_type=os.getenv("DEFAULT_NOTIFICATION_RECIPIENT_TYPE", "role").strip() or "role",
            recipient_ref=os.getenv("DEFAULT_NOTIFICATION_RECIPIENT", "operations").strip() or "operations",
            channel=_configured_notification_channel(),
        )
        return self.repository.get_notification_log(outbox.notification_id)

    def create_expired_notification(self, review_task: ReviewTask, *, timeout_at: datetime) -> NotificationLog | None:
        channel = _configured_notification_channel()
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
        return self.repository.get_notification_log(outbox.notification_id)


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_channel(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized or "unconfigured"


def _configured_notification_channel() -> str:
    return _normalize_channel(os.getenv("DEFAULT_NOTIFICATION_CHANNEL", ""))


def _coerce_datetime_for_comparison(value: datetime, reference: datetime) -> datetime:
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    if value.tzinfo is not None and reference.tzinfo is None:
        return value.replace(tzinfo=None)
    return value


def _review_notification_message(review_task: ReviewTask) -> str:
    label = REVIEW_TYPE_LABELS.get(review_task.review_type, review_task.review_type)
    reason = review_task.reason.strip()
    lines = [f"{label}：{reason}" if reason else label]
    if review_task.platform_name:
        lines.append(f"平台：{review_task.platform_name}")
    scope = review_task.internal_sku or review_task.scope_key
    if scope:
        lines.append(f"对象：{scope}")
    group_id = review_task_group_id(review_task)
    if group_id:
        task_count = review_task.review_payload.get("affected_task_count")
        suffix = f"（{task_count} 条待复核任务）" if task_count else ""
        lines.append(f"任务组：{group_id}{suffix}")
    item_skus = list(
        dict.fromkeys(
            str(item.get("internal_sku") or "").strip()
            for item in review_task.review_payload.get("items", [])
            if isinstance(item, dict)
            and str(item.get("internal_sku") or "").strip()
        )
    )
    if item_skus:
        lines.append(f"商品：{'、'.join(item_skus)}")
    if review_task.required_by is not None:
        lines.append(
            "复核截止："
            + _format_beijing_datetime(review_task.required_by)
        )
    if is_execution_failure_review(review_task):
        lines.append("可选结果：重试任务 / 取消任务")
    return "\n".join(lines)


def _format_beijing_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        display_value = value.replace(tzinfo=BEIJING_TIMEZONE)
    else:
        display_value = value.astimezone(BEIJING_TIMEZONE)
    # Keep non-ASCII text outside strftime's format string. On Windows,
    # strftime may encode the format through the active C locale even when
    # Python UTF-8 mode is enabled.
    return display_value.strftime("%Y-%m-%d %H:%M") + "（北京时间）"


_PAYLOAD_FIELD_WHITELISTS = {
    "mobile_review_required": {
        "review_task_id",
        "review_type",
        "reason",
        "message",
        "scope_type",
        "scope_key",
    },
    "review_expired": {"review_task_id", "message"},
    VERIFICATION_NOTIFICATION_TYPE: {
        "operation_id",
        "execution_attempt_id",
        "message",
        "platform_name",
        "required_by",
        "verification_markers",
    },
}
_GENERIC_PAYLOAD_FIELDS = {"message", "reason", "platform_name", "trade_date"}
_FORBIDDEN_PAYLOAD_KEYS = {
    "token",
    "access_token",
    "authorization",
    "cookie",
    "password",
    "secret",
    "webhook_url",
}
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/=-]+|(?:authorization|cookie|password|access[_-]?token|webhook[_-]?url)\s*[:=]|https?://\S*(?:webhook|/hook/)\S*)"
)


def _sanitize_payload(notification_type: str, value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("notification payload must be an object")
    allowed = _PAYLOAD_FIELD_WHITELISTS.get(notification_type, _GENERIC_PAYLOAD_FIELDS)
    result: dict[str, object] = {}
    for key, nested in value.items():
        normalized_key = str(key).strip()
        lowered_key = normalized_key.lower()
        if (
            lowered_key in _FORBIDDEN_PAYLOAD_KEYS
            or any(word in lowered_key for word in ("token", "password", "secret"))
        ):
            raise ValueError(f"notification payload contains forbidden secret field: payload.{normalized_key}")
        if normalized_key not in allowed:
            raise ValueError(
                f"notification payload field is not allowed for {notification_type}: {normalized_key}"
            )
        sanitized_value = _sanitize_payload_value(nested, path=f"payload.{normalized_key}")
        _validate_payload_field_type(notification_type, normalized_key, sanitized_value)
        result[normalized_key] = sanitized_value
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_NOTIFICATION_PAYLOAD_BYTES:
        raise ValueError("notification payload exceeds the maximum size")
    return result


def _validate_payload_field_type(notification_type: str, key: str, value: object) -> None:
    string_fields = {
        "operation_id",
        "execution_attempt_id",
        "platform_name",
        "required_by",
        "review_task_id",
        "review_type",
        "reason",
        "message",
        "scope_type",
        "scope_key",
        "trade_date",
    }
    if key in string_fields and not isinstance(value, str):
        raise ValueError(f"notification payload field must be a string: {notification_type}.{key}")
    if key == "verification_markers":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError("notification payload field must be a string list: verification_markers")


def _sanitize_payload_value(value: object, *, path: str, depth: int = 0) -> object:
    if depth > 3:
        raise ValueError(f"notification payload nesting is too deep: {path}")
    if isinstance(value, str):
        if len(value) > 2000:
            raise ValueError(f"notification payload value is too long: {path}")
        if _SECRET_VALUE_PATTERN.search(value):
            raise ValueError(f"notification payload contains a secret-like value: {path}")
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, nested in value.items():
            normalized_key = str(key).strip()
            lowered_key = normalized_key.lower()
            if (
                lowered_key in _FORBIDDEN_PAYLOAD_KEYS
                or any(word in lowered_key for word in ("token", "password", "secret"))
            ):
                raise ValueError(f"notification payload contains forbidden secret field: {path}.{normalized_key}")
            result[normalized_key] = _sanitize_payload_value(
                nested,
                path=f"{path}.{normalized_key}",
                depth=depth + 1,
            )
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > 20:
            raise ValueError(f"notification payload list is too long: {path}")
        return [
            _sanitize_payload_value(nested, path=f"{path}[{index}]", depth=depth + 1)
            for index, nested in enumerate(value)
        ]
    raise ValueError(f"notification payload contains unsupported value at {path}")


def _reject_secrets(value: object, *, _path: str = "payload") -> None:
    """Compatibility validator retained for callers outside the service."""

    _sanitize_payload_value(value, path=_path)


def _assert_idempotent_notification_match(
    candidate: NotificationOutbox,
    existing: NotificationOutbox,
) -> None:
    candidate_identity = {
        "notification_key": candidate.notification_key,
        "notification_type": candidate.notification_type,
        "related_task_id": candidate.related_task_id,
        "related_review_task_id": candidate.related_review_task_id,
        "recipient_type": candidate.recipient_type,
        "recipient_ref": candidate.recipient_ref,
        "channel": candidate.channel,
        "priority": int(candidate.priority),
        "max_attempts": int(candidate.max_attempts),
        "deadline_at": candidate.deadline_at.isoformat() if candidate.deadline_at else None,
        "payload_fingerprint": _fingerprint(candidate.payload),
    }
    existing_identity = {
        "notification_key": existing.notification_key,
        "notification_type": existing.notification_type,
        "related_task_id": existing.related_task_id,
        "related_review_task_id": existing.related_review_task_id,
        "recipient_type": existing.recipient_type,
        "recipient_ref": existing.recipient_ref,
        "channel": existing.channel,
        "priority": int(existing.priority),
        "max_attempts": int(existing.max_attempts),
        "deadline_at": existing.deadline_at.isoformat() if existing.deadline_at else None,
        "payload_fingerprint": _fingerprint(existing.payload),
    }
    if candidate_identity != existing_identity:
        raise NotificationIdempotencyConflictError(
            "notification_key is already bound to a different immutable event"
        )


def _assert_duplicate_review_match(
    candidate: ReviewTask,
    existing: ReviewTask,
    candidate_notification: NotificationOutbox,
    existing_notification: NotificationOutbox,
) -> None:
    candidate_identity = _review_event_identity(candidate, candidate_notification)
    existing_identity = _review_event_identity(existing, existing_notification)
    if candidate_identity != existing_identity:
        raise NotificationIdempotencyConflictError(
            "review dedupe_key is already bound to a different pending review event"
        )


def _review_event_identity(review: ReviewTask, notification: NotificationOutbox) -> str:
    identity = {
        "dedupe_key": review.dedupe_key,
        "trade_date": review.trade_date.isoformat() if review.trade_date else None,
        "scope_type": review.scope_type,
        "scope_key": review.scope_key,
        "source_task_id": review.source_task_id,
        "review_type": review.review_type,
        "internal_sku": review.internal_sku,
        "platform_name": review.platform_name,
        "reason": review.reason,
        "required_by": review.required_by.isoformat() if review.required_by else None,
        "review_payload_fingerprint": _fingerprint(review.review_payload),
        "channel": notification.channel,
        "recipient_type": notification.recipient_type,
        "recipient_ref": notification.recipient_ref,
    }
    return _fingerprint(identity)


def _build_feishu_outbox_body(notification: NotificationOutbox, message_type: str) -> dict[str, object]:
    payload = notification.payload if isinstance(notification.payload, dict) else {}
    message = str(payload.get("message") or payload.get("reason") or notification.notification_type)[:2000]
    mobile_review_url = str(payload.get("mobile_review_url") or "").strip()
    if message_type == "post":
        content = [[{"tag": "text", "text": message}]]
        if mobile_review_url:
            content.append(
                [
                    {
                        "tag": "a",
                        "text": "打开手机复核",
                        "href": mobile_review_url,
                    }
                ]
            )
        return {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": str(payload.get("title") or "PRA 人工复核")[:200],
                        "content": content,
                    }
                }
            },
        }
    if mobile_review_url:
        message = f"{message}\n打开手机复核：{mobile_review_url}"
    return {"msg_type": "text", "content": {"text": message}}


def _feishu_response_summary(status_code: int, response_text: str) -> dict[str, object]:
    try:
        parsed = json.loads(response_text)
    except (TypeError, ValueError):
        return {"status_code": status_code, "valid_json": False}
    if not isinstance(parsed, dict):
        return {"status_code": status_code, "valid_json": False}
    allowed_keys = {"code", "msg", "StatusCode", "Message", "request_id", "RequestId"}
    return {
        "status_code": status_code,
        "valid_json": True,
        **{
            key: str(parsed[key])[:200]
            for key in allowed_keys
            if key in parsed and parsed[key] is not None
        },
    }


def _feishu_timeout_seconds() -> float:
    try:
        return max(1.0, min(float(os.getenv("FEISHU_TIMEOUT_SECONDS", "10")), 30.0))
    except ValueError:
        return 10.0

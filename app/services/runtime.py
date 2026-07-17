from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import os
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from app.enums import NotificationSendStatus, ReviewTaskStatus, TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.models import (
    ExecutionLog,
    NotificationLog,
    NotificationSendResult,
    ReviewTask,
    ReviewToken,
    Task,
    TaskStatusHistory,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.execution import ExecutionSimulationService
from app.services.feishu import build_feishu_signature, is_feishu_success_response
from app.services.manual_intervention import MANUAL_INTERVENTION_ACTIONS
from app.services.notification_outbox import (
    FakeSender,
    NotificationOutboxService,
    NotificationOutboxWorker,
    OutboxReviewNotificationService,
    ScriptedSender,
)
from app.utils import serialize_decimal, utc_now


DEFAULT_RUNTIME_DB = Path("data/runtime/pra_runtime.sqlite3")
FEISHU_DISPLAY_TIMEZONE = timezone(timedelta(hours=8))

ALLOWED_TASK_TRANSITIONS = {
    TaskStatus.PENDING: {
        TaskStatus.RUNNING,
        TaskStatus.MANUAL_REVIEW,
        TaskStatus.SKIPPED,
        TaskStatus.CANCELLED,
        TaskStatus.EXPIRED,
    },
    TaskStatus.RUNNING: {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.MANUAL_REVIEW},
    TaskStatus.FAILED: {TaskStatus.PENDING, TaskStatus.CANCELLED},
    TaskStatus.MANUAL_REVIEW: {
        TaskStatus.PENDING,
        TaskStatus.SKIPPED,
        TaskStatus.CANCELLED,
        TaskStatus.EXPIRED,
    },
}

TERMINAL_TASK_STATUSES = {
    TaskStatus.SUCCESS,
    TaskStatus.SKIPPED,
    TaskStatus.CANCELLED,
    TaskStatus.EXPIRED,
}

ALLOWED_REVIEW_TRANSITIONS = {
    ReviewTaskStatus.PENDING: {
        ReviewTaskStatus.APPROVED,
        ReviewTaskStatus.REJECTED,
        ReviewTaskStatus.ADJUSTED,
        ReviewTaskStatus.EXPIRED,
        ReviewTaskStatus.CANCELLED,
    }
}

DEFAULT_MOBILE_REVIEW_ACTIONS = [
    ReviewTaskStatus.APPROVED.value,
    ReviewTaskStatus.REJECTED.value,
    ReviewTaskStatus.ADJUSTED.value,
    ReviewTaskStatus.CANCELLED.value,
]

FEISHU_WEBHOOK_URL_REQUIRED = "FEISHU_WEBHOOK_URL is required for feishu notification"
FEISHU_TOKEN_URL_CREATION_FAILED = "mobile_review_url creation failed"
FEISHU_MESSAGE_TYPE_INVALID = "FEISHU_MESSAGE_TYPE must be 'post' or 'text'"
FEISHU_POST_REVIEW_LINK_TEXT = "👉 点击处理复核"

FEISHU_REVIEW_TYPE_LABELS = {
    "manual_review": "人工复核",
    "manual_price_review": "人工价格复核",
    "below_break_even_review": "低于保本价复核",
    "labor_required": "临时工确认",
    "capacity_warning": "产能预警",
    "shortage_warning": "短缺预警",
    "cold_storage_warning": "冷库预警",
    "clearance_warning": "清库存预警",
}

FEISHU_SCOPE_TYPE_LABELS = {
    "global": "全局事项",
    "forecast_group": "预测分组",
    "sku": "单个商品",
    "platform": "单个平台",
    "task": "单个任务",
    "system": "系统",
}


class RuntimeTaskService:
    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository

    def init_schema(self) -> None:
        self.repository.init_schema()

    def create_tasks(self, tasks: list[Task], *, trade_date: date | None = None) -> int:
        return len(self.create_tasks_returning_inserted(tasks, trade_date=trade_date))

    def create_tasks_returning_inserted(self, tasks: list[Task], *, trade_date: date | None = None) -> list[Task]:
        inserted: list[Task] = []
        for task in tasks:
            normalized = self._normalize_task(task, trade_date=trade_date)
            if self.repository.insert_task(normalized) == 1:
                inserted.append(normalized)
        return inserted

    def list_tasks(
        self,
        *,
        trade_date: date | None = None,
        status: TaskStatus | None = None,
        action_type: TaskActionType | None = None,
        scope_type: str | None = None,
        scope_key: str | None = None,
    ) -> list[Task]:
        return self.repository.list_tasks(
            trade_date=trade_date,
            status=status,
            action_type=action_type,
            scope_type=scope_type,
            scope_key=scope_key,
        )

    def change_status(
        self,
        *,
        task_id: str,
        to_status: TaskStatus,
        changed_by: str,
        reason: str = "",
        metadata: dict[str, object] | None = None,
        result_message: str = "",
    ) -> Task:
        task = self.repository.get_task(task_id)
        if task is None:
            raise ValidationError(f"task not found: {task_id}")
        self._validate_transition(task.task_status, to_status)
        history = TaskStatusHistory(
            history_id=uuid4().hex[:12],
            task_id=task_id,
            from_status=task.task_status,
            to_status=to_status,
            changed_by=changed_by,
            changed_at=datetime.now(),
            reason=reason,
            metadata=metadata or {},
        )
        self.repository.update_task_status_with_history(
            task_id,
            to_status,
            history=history,
            result_message=result_message,
        )
        updated = self.repository.get_task(task_id)
        if updated is None:
            raise ValidationError(f"task not found after status update: {task_id}")
        return updated

    def list_status_history(self, task_id: str) -> list[TaskStatusHistory]:
        return self.repository.list_task_status_history(task_id)

    def get_task(self, task_id: str) -> Task | None:
        return self.repository.get_task(task_id)

    def _validate_transition(self, from_status: TaskStatus, to_status: TaskStatus) -> None:
        if from_status == to_status:
            return
        allowed = ALLOWED_TASK_TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise ValidationError(f"invalid task status transition: {from_status.value} -> {to_status.value}")

    def _normalize_task(self, task: Task, *, trade_date: date | None) -> Task:
        resolved_trade_date = task.trade_date or trade_date
        scope_type, scope_key, internal_sku = _resolve_scope(task, resolved_trade_date)
        dedupe_key = task.dedupe_key or _build_task_dedupe_key(
            trade_date=resolved_trade_date,
            scope_type=scope_type,
            scope_key=scope_key,
            task=task,
            internal_sku=internal_sku,
        )
        created_at = task.created_at
        return replace(
            task,
            trade_date=resolved_trade_date,
            scope_type=scope_type,
            scope_key=scope_key,
            dedupe_key=dedupe_key,
            internal_sku=internal_sku,
            platform_name=task.platform_name or None,
            scheduled_at=task.scheduled_at,
            expires_at=task.expires_at or task.required_by,
            updated_at=task.updated_at or created_at,
        )


class ReviewTaskService:
    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        *,
        runtime_task_service: RuntimeTaskService | None = None,
        notification_service: "ReviewNotificationService | OutboxReviewNotificationService | None" = None,
    ) -> None:
        self.repository = repository
        self.runtime_task_service = runtime_task_service or RuntimeTaskService(repository)
        self.notification_service = notification_service or OutboxReviewNotificationService(repository)

    def create_from_tasks(self, tasks: list[Task], *, trade_date: date | None = None) -> "ReviewTaskCreationSummary":
        review_tasks = [
            _review_task_from_source(task, trade_date=trade_date)
            for task in tasks
            if task.action_type in MANUAL_INTERVENTION_ACTIONS
        ]
        inserted_review_tasks: list[ReviewTask] = []
        inserted_review_tasks_count = 0
        inserted_notification_logs_count = 0
        notification_errors: list[str] = []
        for review_task in review_tasks:
            if isinstance(self.notification_service, OutboxReviewNotificationService):
                try:
                    inserted_count, outbox_count, _ = self.notification_service.create_review_task_atomically(
                        review_task
                    )
                except Exception as exc:
                    notification_errors.append(f"{review_task.review_task_id}: {exc}")
                    continue
                if inserted_count != 1:
                    continue
                inserted_review_tasks_count += 1
                inserted_review_tasks.append(review_task)
                inserted_notification_logs_count += outbox_count
                continue
            inserted_count = self.repository.insert_review_tasks([review_task])
            if inserted_count != 1:
                continue
            inserted_review_tasks_count += 1
            inserted_review_tasks.append(review_task)
            if review_task.review_status != ReviewTaskStatus.PENDING:
                continue
            try:
                created = self.notification_service.create_initial_notification(review_task)
            except Exception as exc:
                notification_errors.append(f"{review_task.review_task_id}: {exc}")
                continue
            if created is not None:
                inserted_notification_logs_count += 1
                if created.send_status == NotificationSendStatus.FAILED.value:
                    notification_errors.append(
                        f"{review_task.review_task_id}: notification failed: {created.error_message or 'send_status=failed'}"
                    )
        return ReviewTaskCreationSummary(
            inserted_review_tasks_count=inserted_review_tasks_count,
            inserted_notification_logs_count=inserted_notification_logs_count,
            review_tasks=inserted_review_tasks,
            notification_errors=notification_errors,
        )

    def list_review_tasks(
        self,
        *,
        trade_date: date | None = None,
        status: ReviewTaskStatus | None = None,
    ) -> list[ReviewTask]:
        return self.repository.list_review_tasks(trade_date=trade_date, status=status)

    def get_review_task(self, review_task_id: str) -> ReviewTask | None:
        return self.repository.get_review_task(review_task_id)

    def resolve_review_task(
        self,
        *,
        review_task_id: str,
        status: ReviewTaskStatus,
        actor: str,
        actor_source: str = "manual_operator",
        note: str = "",
        resolution_payload: dict[str, object] | None = None,
        source_task_status: TaskStatus | None = None,
        source_task_metadata_extra: dict[str, object] | None = None,
    ) -> ReviewTask:
        review_task = self.repository.get_review_task(review_task_id)
        if review_task is None:
            raise ValidationError(f"review task not found: {review_task_id}")
        if review_task.review_status != ReviewTaskStatus.PENDING:
            raise ValidationError(f"review task already handled: {review_task_id}")
        self._validate_transition(review_task.review_status, status)
        now = datetime.now()
        resolved_payload = resolution_payload or {}
        updated = replace(
            review_task,
            review_status=status,
            resolution_payload=resolved_payload,
            updated_at=now,
            resolved_by=actor,
            resolved_at=now,
            resolution_note=note,
        )
        source_task_id: str | None = None
        source_history: TaskStatusHistory | None = None
        if source_task_status is not None and review_task.source_task_id:
            source_task = self.runtime_task_service.get_task(review_task.source_task_id)
            if source_task is not None and source_task.task_status in {TaskStatus.MANUAL_REVIEW, TaskStatus.PENDING}:
                self.runtime_task_service._validate_transition(source_task.task_status, source_task_status)
                metadata = {
                    "review_task_id": review_task_id,
                    "review_status": status.value,
                    "actor": actor,
                    "actor_source": actor_source,
                    "resolution_note": note,
                    "resolution_payload_summary": _resolution_payload_summary(resolved_payload),
                }
                if source_task_metadata_extra:
                    metadata.update(source_task_metadata_extra)
                source_task_id = review_task.source_task_id
                source_history = TaskStatusHistory(
                    history_id=uuid4().hex[:12],
                    task_id=source_task_id,
                    from_status=source_task.task_status,
                    to_status=source_task_status,
                    changed_by=actor,
                    changed_at=datetime.now(),
                    reason=f"review_task:{review_task_id}:{status.value}",
                    metadata=metadata,
                )
        self.repository.update_review_task_with_optional_task_status(
            updated,
            task_id=source_task_id,
            task_status=source_task_status if source_task_id else None,
            history=source_history,
            result_message=note,
        )
        return updated

    def expire_pending_review_tasks(
        self,
        *,
        now: datetime | None = None,
        apply: bool = False,
        actor: str = "system:expire_review_tasks",
        enable_notification: bool = False,
    ) -> "ExpireReviewTasksSummary":
        cutoff = now or datetime.now()
        pending_reviews = self.repository.list_pending_review_tasks_due_before(cutoff)
        summary = ExpireReviewTasksSummary(scanned_review_tasks=len(pending_reviews))
        for review in pending_reviews:
            source_task = self.runtime_task_service.get_task(review.source_task_id) if review.source_task_id else None
            source_task_status = None
            if source_task is not None and source_task.task_status == TaskStatus.MANUAL_REVIEW:
                source_task_status = TaskStatus.EXPIRED
            elif review.source_task_id:
                summary.skipped_source_tasks += 1
                summary.errors.append(
                    f"{review.review_task_id}: source task not advanced because status is "
                    f"{source_task.task_status.value if source_task else 'missing'}"
                )

            resolution_payload = {
                "timeout_at": cutoff.isoformat(),
                "required_by": review.required_by.isoformat() if review.required_by else None,
                "timeout_reason": "required_by passed",
                "timeout_policy": "uniform_conservative_v1",
            }
            metadata_extra = {
                "review_type": review.review_type,
                "required_by": review.required_by.isoformat() if review.required_by else None,
                "timeout_at": cutoff.isoformat(),
                "timeout_reason": "required_by passed",
                "timeout_policy": "uniform_conservative_v1",
            }
            if review.review_type in {
                TaskActionType.LABOR_REQUIRED.value,
                TaskActionType.CAPACITY_WARNING.value,
            }:
                resolution_payload["fallback_to_safe_default"] = True
                resolution_payload["confirmed_temp_worker_count"] = 0
                resolution_payload["confirmed_packing_capacity_qty"] = 250
                metadata_extra["fallback_to_safe_default"] = True
                metadata_extra["confirmed_temp_worker_count"] = 0
                metadata_extra["confirmed_packing_capacity_qty"] = 250

            if not apply:
                summary.expired_review_tasks += 1
                if source_task_status is not None:
                    summary.expired_source_tasks += 1
                continue

            if enable_notification and isinstance(self.notification_service, OutboxReviewNotificationService):
                resolved = replace(
                    review,
                    review_status=ReviewTaskStatus.EXPIRED,
                    resolution_payload=resolution_payload,
                    updated_at=cutoff,
                    resolved_by=actor,
                    resolved_at=cutoff,
                    resolution_note="expired by required_by timeout",
                )
                history = None
                source_task_id = None
                if source_task_status is not None and source_task is not None:
                    source_task_id = source_task.task_id
                    history = TaskStatusHistory(
                        history_id=uuid4().hex[:12],
                        task_id=source_task.task_id,
                        from_status=source_task.task_status,
                        to_status=source_task_status,
                        changed_by=actor,
                        changed_at=cutoff,
                        reason=f"review_task:{review.review_task_id}:{ReviewTaskStatus.EXPIRED.value}",
                        metadata={
                            "review_task_id": review.review_task_id,
                            "review_status": ReviewTaskStatus.EXPIRED.value,
                            "actor": actor,
                            "actor_source": "system_timeout",
                            "resolution_note": "expired by required_by timeout",
                            "resolution_payload_summary": _resolution_payload_summary(resolution_payload),
                            **metadata_extra,
                        },
                    )
                notification, compatibility_log = (
                    self.notification_service.outbox_service.build_expired_notification_candidate(
                        resolved,
                        timeout_at=cutoff,
                    )
                )
                updated_count, outbox_count = self.repository.expire_review_task_with_notification_outbox(
                    resolved,
                    notification,
                    compatibility_log,
                    task_id=source_task_id,
                    task_status=source_task_status if source_task_id else None,
                    history=history,
                    result_message="expired by required_by timeout",
                )
                if updated_count != 1:
                    continue
                summary.notification_logs_created += outbox_count
            else:
                resolved = self.resolve_review_task(
                    review_task_id=review.review_task_id,
                    status=ReviewTaskStatus.EXPIRED,
                    actor=actor,
                    actor_source="system_timeout",
                    note="expired by required_by timeout",
                    resolution_payload=resolution_payload,
                    source_task_status=source_task_status,
                    source_task_metadata_extra=metadata_extra,
                )
            summary.expired_review_tasks += 1
            if source_task_status is not None:
                summary.expired_source_tasks += 1
            if enable_notification and not isinstance(self.notification_service, OutboxReviewNotificationService):
                try:
                    created = self.notification_service.create_expired_notification(resolved, timeout_at=cutoff)
                except Exception as exc:
                    summary.errors.append(f"{review.review_task_id}: expire notification failed: {exc}")
                else:
                    if created is not None:
                        summary.notification_logs_created += 1
        return summary

    def _validate_transition(self, from_status: ReviewTaskStatus, to_status: ReviewTaskStatus) -> None:
        if from_status == to_status:
            return
        allowed = ALLOWED_REVIEW_TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise ValidationError(f"invalid review status transition: {from_status.value} -> {to_status.value}")


@dataclass(slots=True)
class ReviewTokenCreationResult:
    review_token: ReviewToken
    raw_token: str
    mobile_review_url: str


@dataclass(slots=True)
class ReviewTokenValidationResult:
    is_valid: bool
    failure_reason: str
    review_token: ReviewToken | None = None
    review_task: ReviewTask | None = None
    token_subject: str = ""


class ReviewTokenService:
    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository

    def create_token(
        self,
        review_task_id: str,
        *,
        token_subject: str = "operations",
        allowed_actions: list[str] | None = None,
        expires_at: datetime | None = None,
        created_by: str = "system",
        note: str | None = None,
    ) -> ReviewTokenCreationResult:
        review_task = self.repository.get_review_task(review_task_id)
        if review_task is None:
            raise ValidationError(f"review task not found: {review_task_id}")
        if review_task.review_status != ReviewTaskStatus.PENDING:
            raise ValidationError(f"cannot create review token for non-pending review task: {review_task_id}")

        raw_token = secrets.token_urlsafe(32)
        token_hash = self._hash_raw_token(raw_token)
        resolved_actions = allowed_actions or DEFAULT_MOBILE_REVIEW_ACTIONS
        now = datetime.now()
        review_token = ReviewToken(
            token_id=uuid4().hex[:12],
            review_task_id=review_task_id,
            token_hash=token_hash,
            token_subject=token_subject,
            allowed_actions=resolved_actions,
            expires_at=expires_at or self._default_expires_at(review_task, now),
            created_at=now,
            created_by=created_by,
            note=note,
        )
        inserted = self.repository.insert_review_token(review_token)
        if inserted != 1:
            raise ValidationError("review token was not inserted")
        return ReviewTokenCreationResult(
            review_token=review_token,
            raw_token=raw_token,
            mobile_review_url=self.build_mobile_review_url(review_task_id, raw_token),
        )

    def validate_token(self, review_task_id: str, raw_token: str, action: str | None = None) -> ReviewTokenValidationResult:
        try:
            token_hash = self._hash_raw_token(raw_token)
        except ValidationError as exc:
            return ReviewTokenValidationResult(is_valid=False, failure_reason=str(exc))

        review_token = self.repository.get_review_token_by_hash(token_hash)
        if review_token is None:
            return ReviewTokenValidationResult(is_valid=False, failure_reason="token not found")
        review_task = self.repository.get_review_task(review_task_id)
        if review_task is None:
            return ReviewTokenValidationResult(
                is_valid=False,
                failure_reason=f"review task not found: {review_task_id}",
                review_token=review_token,
                token_subject=review_token.token_subject,
            )
        if review_token.review_task_id != review_task_id:
            return self._invalid("token review_task_id mismatch", review_token, review_task)
        now = datetime.now()
        if review_token.expires_at <= now:
            return self._invalid("token expired", review_token, review_task)
        if review_token.revoked_at is not None:
            return self._invalid("token revoked", review_token, review_task)
        if review_token.used_at is not None:
            return self._invalid("token already used", review_token, review_task)
        if review_task.review_status != ReviewTaskStatus.PENDING:
            return self._invalid("review task is not pending", review_token, review_task)
        if action is not None:
            if action not in review_token.allowed_actions:
                return self._invalid("action not allowed by token", review_token, review_task)
            if action not in _allowed_mobile_actions_for_review_type(review_task.review_type):
                return self._invalid("action not allowed by review_type", review_token, review_task)
        return ReviewTokenValidationResult(
            is_valid=True,
            failure_reason="",
            review_token=review_token,
            review_task=review_task,
            token_subject=review_token.token_subject,
        )

    def record_detail_access(self, token_id: str) -> ReviewToken:
        token = self.repository.get_review_token(token_id)
        if token is None:
            raise ValidationError(f"review token not found: {token_id}")
        now = datetime.now()
        self.repository.update_review_token_usage(token_id, last_used_at=now)
        updated = self.repository.get_review_token(token_id)
        if updated is None:
            raise ValidationError(f"review token not found after update: {token_id}")
        return updated

    def record_resolve_usage(self, token_id: str) -> ReviewToken:
        token = self.repository.get_review_token(token_id)
        if token is None:
            raise ValidationError(f"review token not found: {token_id}")
        if token.used_at is not None:
            raise ValidationError(f"review token already used: {token_id}")
        now = datetime.now()
        self.repository.update_review_token_usage(token_id, used_at=now, last_used_at=now)
        updated = self.repository.get_review_token(token_id)
        if updated is None:
            raise ValidationError(f"review token not found after update: {token_id}")
        return updated

    def revoke_token(self, token_id: str, revoked_at: datetime | None = None) -> ReviewToken:
        token = self.repository.get_review_token(token_id)
        if token is None:
            raise ValidationError(f"review token not found: {token_id}")
        self.repository.revoke_review_token(token_id, revoked_at or datetime.now())
        updated = self.repository.get_review_token(token_id)
        if updated is None:
            raise ValidationError(f"review token not found after revoke: {token_id}")
        return updated

    def revoke_tokens_for_review_task(self, review_task_id: str, revoked_at: datetime | None = None) -> int:
        return self.repository.revoke_review_tokens_by_review_task_id(review_task_id, revoked_at or datetime.now())

    def build_mobile_review_url(self, review_task_id: str, raw_token: str) -> str:
        path = f"/mobile/review/{quote(review_task_id)}?token={quote(raw_token)}"
        base_url = os.getenv("MOBILE_REVIEW_BASE_URL", "").strip()
        if not base_url:
            return path
        return f"{base_url.rstrip('/')}{path}"

    def _hash_raw_token(self, raw_token: str) -> str:
        secret = os.getenv("REVIEW_TOKEN_SECRET", "").strip()
        if not secret:
            raise ValidationError("REVIEW_TOKEN_SECRET is required")
        digest = hmac.new(secret.encode("utf-8"), raw_token.encode("utf-8"), hashlib.sha256)
        return digest.hexdigest()

    def _default_expires_at(self, review_task: ReviewTask, now: datetime) -> datetime:
        default_expiry = now + timedelta(hours=24)
        if review_task.required_by is None:
            return default_expiry
        return min(review_task.required_by, default_expiry)

    def _invalid(
        self,
        reason: str,
        review_token: ReviewToken,
        review_task: ReviewTask,
    ) -> ReviewTokenValidationResult:
        return ReviewTokenValidationResult(
            is_valid=False,
            failure_reason=reason,
            review_token=review_token,
            review_task=review_task,
            token_subject=review_token.token_subject,
        )


class NotificationLogService:
    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository

    def append(self, log: NotificationLog) -> int:
        return self.repository.insert_notification_logs([log])

    def list_logs(
        self,
        *,
        related_review_task_id: str | None = None,
        send_status: str | None = None,
        channel: str | None = None,
    ) -> list[NotificationLog]:
        return self.repository.list_notification_logs(
            related_review_task_id=related_review_task_id,
            send_status=send_status,
            channel=channel,
        )

    def get_log(self, notification_id: str) -> NotificationLog | None:
        return self.repository.get_notification_log(notification_id)


class NotificationSender(Protocol):
    channel: str

    def send(self, log: NotificationLog, payload: dict[str, object] | None = None) -> NotificationSendResult:
        ...


class MockNotificationSender:
    channel = "mock"

    def send(self, log: NotificationLog, payload: dict[str, object] | None = None) -> NotificationSendResult:
        return NotificationSendResult(
            send_status=NotificationSendStatus.SUCCESS.value,
            sent_at=datetime.now(),
            raw_response_json={"mock": True},
        )


class FailedNotificationSender:
    def __init__(self, channel: str, error_message: str) -> None:
        self.channel = channel
        self.error_message = error_message

    def send(self, log: NotificationLog, payload: dict[str, object] | None = None) -> NotificationSendResult:
        return NotificationSendResult(
            send_status=NotificationSendStatus.FAILED.value,
            sent_at=None,
            error_message=self.error_message,
            raw_response_json={"error": self.error_message, "channel": self.channel},
        )


class FeishuWebhookNotificationSender:
    channel = "feishu"

    def send(self, log: NotificationLog, payload: dict[str, object] | None = None) -> NotificationSendResult:
        webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
        if not webhook_url:
            return NotificationSendResult(
                send_status=NotificationSendStatus.FAILED.value,
                sent_at=None,
                error_message=FEISHU_WEBHOOK_URL_REQUIRED,
                raw_response_json={"error": FEISHU_WEBHOOK_URL_REQUIRED},
            )

        message_type = _feishu_message_type()
        if message_type not in {"post", "text"}:
            return NotificationSendResult(
                send_status=NotificationSendStatus.FAILED.value,
                sent_at=None,
                error_message=FEISHU_MESSAGE_TYPE_INVALID,
                raw_response_json={"error": FEISHU_MESSAGE_TYPE_INVALID, "message_type": message_type},
            )

        request_body = self._build_request_body(log, payload, message_type)
        encoded_body = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
        request = Request(
            webhook_url,
            data=encoded_body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=_feishu_timeout_seconds()) as response:
                status_code = response.getcode()
                response_text = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            response_text = exc.read().decode("utf-8", errors="replace")
            return NotificationSendResult(
                send_status=NotificationSendStatus.FAILED.value,
                sent_at=None,
                error_message=f"feishu webhook HTTP {exc.code}",
                raw_response_json=_feishu_response_summary(status_code=exc.code, response_text=response_text),
            )
        except (TimeoutError, URLError, OSError) as exc:
            return NotificationSendResult(
                send_status=NotificationSendStatus.FAILED.value,
                sent_at=None,
                error_message=f"feishu webhook request failed: {type(exc).__name__}: {exc}",
                raw_response_json={"error_type": type(exc).__name__, "error": str(exc)},
            )

        response_json = _feishu_response_summary(status_code=status_code, response_text=response_text)
        if status_code < 200 or status_code >= 300:
            return NotificationSendResult(
                send_status=NotificationSendStatus.FAILED.value,
                sent_at=None,
                error_message=f"feishu webhook HTTP {status_code}",
                raw_response_json=response_json,
            )
        if not _is_feishu_success_response(response_json):
            return NotificationSendResult(
                send_status=NotificationSendStatus.FAILED.value,
                sent_at=None,
                error_message=_feishu_error_message(response_json),
                raw_response_json=response_json,
            )

        return NotificationSendResult(
            send_status=NotificationSendStatus.SUCCESS.value,
            sent_at=datetime.now(),
            provider_message_id=str(response_json.get("request_id") or response_json.get("RequestId") or ""),
            raw_response_json=response_json,
        )

    def _build_request_body(
        self,
        log: NotificationLog,
        payload: dict[str, object] | None,
        message_type: str,
    ) -> dict[str, object]:
        # MVP keeps webhook messages short; no rate-limit queue, rich interaction, or retry workflow here.
        if message_type == "post":
            body = _build_feishu_review_notification_post_body(log, payload)
        else:
            body = _build_feishu_review_notification_text_body(log, payload)
        secret = os.getenv("FEISHU_WEBHOOK_SECRET", "").strip()
        if secret:
            timestamp = str(int(time.time()))
            body["timestamp"] = timestamp
            body["sign"] = _build_feishu_sign(timestamp, secret)
        return body


class ReviewNotificationService:
    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        *,
        sender_factory: "NotificationSenderFactory | None" = None,
    ) -> None:
        self.repository = repository
        self.notification_log_service = NotificationLogService(repository)
        self.sender_factory = sender_factory or NotificationSenderFactory()

    def create_initial_notification(self, review_task: ReviewTask) -> NotificationLog | None:
        recipient_type = os.getenv("DEFAULT_NOTIFICATION_RECIPIENT_TYPE", "role").strip() or "role"
        recipient = os.getenv("DEFAULT_NOTIFICATION_RECIPIENT", "operations").strip() or "operations"
        channel = os.getenv("DEFAULT_NOTIFICATION_CHANNEL", "mock").strip() or "mock"
        message, send_payload, pre_send_failure = self._build_initial_notification_content(review_task, channel)
        return self._create_notification(
            review_task,
            recipient_type=recipient_type,
            recipient=recipient,
            channel=channel,
            dedupe_suffix="initial",
            message=message,
            send_payload=send_payload,
            pre_send_failure=pre_send_failure,
        )

    def create_expired_notification(self, review_task: ReviewTask, *, timeout_at: datetime) -> NotificationLog | None:
        recipient_type = os.getenv("DEFAULT_NOTIFICATION_RECIPIENT_TYPE", "role").strip() or "role"
        recipient = os.getenv("DEFAULT_NOTIFICATION_RECIPIENT", "operations").strip() or "operations"
        channel = os.getenv("DEFAULT_NOTIFICATION_CHANNEL", "mock").strip() or "mock"
        return self._create_notification(
            review_task,
            recipient_type=recipient_type,
            recipient=recipient,
            channel=channel,
            dedupe_suffix="expired",
            message=_build_expired_review_notification_message(review_task, timeout_at=timeout_at),
        )

    def _create_notification(
        self,
        review_task: ReviewTask,
        *,
        recipient_type: str,
        recipient: str,
        channel: str,
        dedupe_suffix: str,
        message: str,
        send_payload: dict[str, object] | None = None,
        pre_send_failure: str = "",
    ) -> NotificationLog | None:
        sender = self.sender_factory.build(channel)
        now = datetime.now()
        log = NotificationLog(
            notification_id=uuid4().hex[:12],
            related_task_id=review_task.source_task_id,
            related_review_task_id=review_task.review_task_id,
            recipient_type=recipient_type,
            recipient=recipient,
            channel=sender.channel,
            sent_at=None,
            send_status=NotificationSendStatus.PENDING.value,
            dedupe_key=f"review:{review_task.review_task_id}:{dedupe_suffix}:{sender.channel}:{recipient}",
            message=message,
            created_at=now,
        )
        if pre_send_failure:
            result = NotificationSendResult(
                send_status=NotificationSendStatus.FAILED.value,
                sent_at=None,
                error_message=pre_send_failure,
                raw_response_json={"error": pre_send_failure},
            )
        else:
            result = sender.send(log, send_payload)
        log = replace(
            log,
            send_status=result.send_status,
            sent_at=result.sent_at,
            error_message=result.error_message,
        )
        inserted = self.notification_log_service.append(log)
        return log if inserted == 1 else None

    def _build_initial_notification_content(
        self,
        review_task: ReviewTask,
        channel: str,
    ) -> tuple[str, dict[str, object] | None, str]:
        message = _build_review_notification_message(review_task)
        normalized_channel = channel.strip().lower()
        if normalized_channel == "feishu":
            try:
                token = ReviewTokenService(self.repository).create_token(
                    review_task.review_task_id,
                    created_by="notification_service",
                    note="feishu mobile_review_url notification link",
                )
            except (ValidationError, RuntimeError) as exc:
                return (
                    f"{message} | mobile_review_url_created=false",
                    None,
                    f"{FEISHU_TOKEN_URL_CREATION_FAILED}: {exc}",
                )
            return (
                f"{message} | mobile_review_url_created=true",
                _build_feishu_review_notification_payload(review_task, token.mobile_review_url),
                "",
            )
        if os.getenv("ENABLE_MOBILE_REVIEW_URL_IN_NOTIFICATION", "").strip().lower() != "true":
            return message, None, ""
        try:
            ReviewTokenService(self.repository).create_token(
                review_task.review_task_id,
                created_by="notification_service",
                note="mobile_review_url notification link",
            )
        except (ValidationError, RuntimeError):
            return f"{message} | mobile_review_url_created=false", None, ""
        return f"{message} | mobile_review_url_created=true", None, ""


class NotificationSenderFactory:
    def build(self, channel: str) -> NotificationSender:
        normalized = channel.strip().lower()
        if normalized == "mock":
            return MockNotificationSender()
        if normalized == "feishu":
            return FeishuWebhookNotificationSender()
        return FailedNotificationSender(
            normalized or "unknown",
            f"unsupported notification channel: {channel}",
        )


class ExecutionRuntimeService:
    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository
        self.runtime_task_service = RuntimeTaskService(repository)
        self.execution_service = ExecutionSimulationService()

    def simulate_pending(self, *, executor_name: str = "mock_executor") -> tuple[list[Task], list[ExecutionLog]]:
        tasks = self.repository.list_tasks(status=TaskStatus.PENDING)
        updated_tasks, logs = self.execution_service.simulate(tasks, executor_name=executor_name)
        self.repository.insert_execution_logs(logs)
        for task in updated_tasks:
            self.runtime_task_service.change_status(
                task_id=task.task_id,
                to_status=TaskStatus.RUNNING,
                changed_by=executor_name,
                reason="simulated execution started",
                metadata={"executor_name": executor_name},
            )
            self.runtime_task_service.change_status(
                task_id=task.task_id,
                to_status=task.task_status,
                changed_by=executor_name,
                reason="simulated execution",
                metadata={"executor_name": executor_name},
                result_message=task.result_message,
            )
        return updated_tasks, logs


def create_runtime_repository(db_path: Path | None = None) -> SQLiteRuntimeRepository:
    return SQLiteRuntimeRepository(db_path or DEFAULT_RUNTIME_DB)


class ReviewTaskCreationSummary:
    def __init__(
        self,
        *,
        inserted_review_tasks_count: int,
        inserted_notification_logs_count: int,
        review_tasks: list[ReviewTask],
        notification_errors: list[str],
    ) -> None:
        self.inserted_review_tasks_count = inserted_review_tasks_count
        self.inserted_notification_logs_count = inserted_notification_logs_count
        self.review_tasks = review_tasks
        self.notification_errors = notification_errors


class ExpireReviewTasksSummary:
    def __init__(
        self,
        *,
        scanned_review_tasks: int,
        expired_review_tasks: int = 0,
        expired_source_tasks: int = 0,
        skipped_source_tasks: int = 0,
        notification_logs_created: int = 0,
        errors: list[str] | None = None,
    ) -> None:
        self.scanned_review_tasks = scanned_review_tasks
        self.expired_review_tasks = expired_review_tasks
        self.expired_source_tasks = expired_source_tasks
        self.skipped_source_tasks = skipped_source_tasks
        self.notification_logs_created = notification_logs_created
        self.errors = errors or []


def _resolve_scope(task: Task, trade_date: date | None) -> tuple[str, str, str | None]:
    if task.scope_key and task.scope_type:
        internal_sku = None if task.internal_sku == "__operation__" else task.internal_sku
        return task.scope_type, task.scope_key, internal_sku
    if task.internal_sku in (None, "", "__operation__"):
        scope_key = trade_date.isoformat() if trade_date is not None else "global"
        return "global", scope_key, None
    return "sku", str(task.internal_sku), task.internal_sku


def _build_task_dedupe_key(
    *,
    trade_date: date | None,
    scope_type: str,
    scope_key: str,
    task: Task,
    internal_sku: str | None,
) -> str:
    return "|".join(
        [
            trade_date.isoformat() if trade_date is not None else "none",
            scope_type,
            scope_key,
            task.action_type.value,
            task.platform_name or "",
            internal_sku or "",
            task.target_status or "",
            serialize_decimal(task.target_price) or "",
        ]
    )


def _review_task_from_source(task: Task, *, trade_date: date | None) -> ReviewTask:
    resolved_trade_date = task.trade_date or trade_date
    scope_type, scope_key, internal_sku = _resolve_scope(task, resolved_trade_date)
    dedupe_key = "review|" + (
        task.dedupe_key
        or _build_task_dedupe_key(
            trade_date=resolved_trade_date,
            scope_type=scope_type,
            scope_key=scope_key,
            task=task,
            internal_sku=internal_sku,
        )
    )
    return ReviewTask(
        review_task_id=uuid4().hex[:12],
        trade_date=resolved_trade_date,
        scope_type=scope_type,
        scope_key=scope_key,
        dedupe_key=dedupe_key,
        source_task_id=task.task_id,
        review_type=task.action_type.value,
        review_status=ReviewTaskStatus.PENDING,
        internal_sku=internal_sku,
        platform_name=task.platform_name,
        reason=task.result_message,
        review_payload={
            "task_id": task.task_id,
            "action_type": task.action_type.value,
            "target_price": str(task.target_price) if isinstance(task.target_price, Decimal) else task.target_price,
            "target_status": task.target_status,
            "decision_trace": task.decision_trace,
        },
        required_by=task.required_by,
        created_at=task.created_at,
        updated_at=task.updated_at or task.created_at,
    )


def _resolution_payload_summary(payload: dict[str, object]) -> dict[str, object]:
    if not payload:
        return {}
    summary: dict[str, object] = {"keys": sorted(payload.keys())}
    if "reviewer_code" in payload and payload.get("reviewer_code"):
        summary["reviewer_code_present"] = True
    if "adjustment" in payload:
        summary["adjustment"] = payload.get("adjustment")
    return summary


def _allowed_mobile_actions_for_review_type(review_type: str) -> list[str]:
    return DEFAULT_MOBILE_REVIEW_ACTIONS


def _feishu_timeout_seconds() -> float:
    raw = os.getenv("FEISHU_WEBHOOK_TIMEOUT_SECONDS", "5").strip()
    try:
        timeout = float(raw)
    except ValueError:
        return 5.0
    return timeout if timeout > 0 else 5.0


def _feishu_message_type() -> str:
    return os.getenv("FEISHU_MESSAGE_TYPE", "post").strip().lower() or "post"


def _build_feishu_sign(timestamp: str, secret: str) -> str:
    return build_feishu_signature(timestamp, secret)


def _feishu_response_summary(*, status_code: int, response_text: str) -> dict[str, object]:
    try:
        parsed = json.loads(response_text) if response_text else {}
    except json.JSONDecodeError:
        parsed = {"raw_text": response_text[:500]}
    if isinstance(parsed, dict):
        parsed.setdefault("http_status", status_code)
        return parsed
    return {"http_status": status_code, "raw_json": parsed}


def _is_feishu_success_response(response_json: dict[str, object]) -> bool:
    return is_feishu_success_response(response_json)


def _feishu_error_message(response_json: dict[str, object]) -> str:
    message = (
        response_json.get("msg")
        or response_json.get("message")
        or response_json.get("StatusMessage")
        or response_json.get("error")
        or "feishu webhook returned failure"
    )
    return str(message)


def _build_feishu_review_notification_payload(review_task: ReviewTask, mobile_review_url: str) -> dict[str, object]:
    return {
        "text": _build_feishu_review_notification_text(review_task, mobile_review_url),
        "review_type": review_task.review_type,
        "review_type_label": _feishu_review_type_label(review_task.review_type),
        "trade_date": review_task.trade_date.isoformat() if review_task.trade_date else "-",
        "scope_type": review_task.scope_type,
        "scope_key": review_task.scope_key,
        "scope_label": _feishu_scope_label(review_task.scope_type, review_task.scope_key),
        "required_by": _format_feishu_datetime(review_task.required_by),
        "reason": _truncate_for_feishu(review_task.reason, 200),
        "mobile_review_url": mobile_review_url,
    }


def _build_feishu_review_notification_text_body(
    log: NotificationLog,
    payload: dict[str, object] | None,
) -> dict[str, object]:
    values = payload or {}
    if values.get("notification_kind") == "shadowbot_login_verification":
        text = "\n".join(
            [
                str(values.get("title") or "ShadowBot 登录验证码人工接管"),
                f"平台：{values.get('platform_name') or '-'}",
                f"执行尝试：{values.get('execution_attempt_id') or '-'}",
                f"截止时间：{values.get('required_by') or '-'}",
                str(values.get("action") or "请在已打开的小程序中完成手机验证码。"),
            ]
        )
    else:
        text = str(values.get("text") or log.message)
    return {
        "msg_type": "text",
        "content": {"text": text},
    }


def _build_feishu_review_notification_post_body(
    log: NotificationLog,
    payload: dict[str, object] | None,
) -> dict[str, object]:
    values = payload or {}
    title = str(values.get("title") or "PRA 复核通知")
    if values.get("notification_kind") == "shadowbot_login_verification":
        content: list[list[dict[str, str]]] = [
            [{"tag": "text", "text": f"平台：{values.get('platform_name') or '-'}"}],
            [{"tag": "text", "text": f"执行尝试：{values.get('execution_attempt_id') or '-'}"}],
            [{"tag": "text", "text": f"截止时间：{values.get('required_by') or '-'}"}],
            [{"tag": "text", "text": str(values.get("action") or "请在已打开的小程序中完成手机验证码。")}],
        ]
        return {
            "msg_type": "post",
            "content": {"post": {"zh_cn": {"title": title, "content": content}}},
        }
    if values.get("system_test"):
        content: list[list[dict[str, str]]] = [
            [{"tag": "text", "text": "说明：这是由 /system 手动触发的测试消息。"}],
            [{"tag": "text", "text": "说明：不关联任何复核任务，不包含手机复核链接。"}],
            [{"tag": "text", "text": f"触发时间：{values.get('triggered_at') or '-'}"}],
            [{"tag": "text", "text": f"当前通知模式：{values.get('notification_mode') or 'feishu'}"}],
        ]
        return {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": content,
                    }
                }
            },
        }
    review_type = str(values.get("review_type") or "-")
    review_type_label = str(values.get("review_type_label") or _feishu_review_type_label(review_type))
    trade_date = str(values.get("trade_date") or "-")
    scope_label = str(
        values.get("scope_label")
        or _feishu_scope_label(str(values.get("scope_type") or "-"), str(values.get("scope_key") or "-"))
    )
    required_by = str(values.get("required_by") or "-")
    reason = _truncate_for_feishu(values.get("reason") or log.message, 200)
    mobile_review_url = str(values.get("mobile_review_url") or "")
    content: list[list[dict[str, str]]] = [
        [{"tag": "text", "text": f"需要处理：{review_type_label}"}],
        [{"tag": "text", "text": f"业务日期：{trade_date}"}],
        [{"tag": "text", "text": f"处理对象：{scope_label}"}],
        [{"tag": "text", "text": f"截止时间：{required_by}"}],
        [{"tag": "text", "text": f"原因：{reason}"}],
    ]
    if mobile_review_url:
        content.append([{"tag": "a", "text": FEISHU_POST_REVIEW_LINK_TEXT, "href": mobile_review_url}])
    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": content,
                }
            }
        },
    }


def _build_feishu_review_notification_text(review_task: ReviewTask, mobile_review_url: str) -> str:
    trade_date = review_task.trade_date.isoformat() if review_task.trade_date else "-"
    required_by = _format_feishu_datetime(review_task.required_by)
    reason = _truncate_for_feishu(review_task.reason, 200)
    return "\n".join(
        [
            "PRA 复核通知",
            f"需要处理：{_feishu_review_type_label(review_task.review_type)}",
            f"业务日期：{trade_date}",
            f"处理对象：{_feishu_scope_label(review_task.scope_type, review_task.scope_key)}",
            f"截止时间：{required_by}",
            f"原因：{reason}",
            f"{FEISHU_POST_REVIEW_LINK_TEXT}：{mobile_review_url}",
        ]
    )


def _feishu_review_type_label(review_type: str) -> str:
    return FEISHU_REVIEW_TYPE_LABELS.get(review_type, review_type)


def _feishu_scope_label(scope_type: str, scope_key: str | None) -> str:
    return f"{FEISHU_SCOPE_TYPE_LABELS.get(scope_type, scope_type)}：{scope_key or '-'}"


def _format_feishu_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    display_value = value
    if value.tzinfo is not None and value.utcoffset() is not None:
        display_value = value.astimezone(FEISHU_DISPLAY_TIMEZONE)
    return display_value.strftime("%Y-%m-%d %H:%M")


def _truncate_for_feishu(value: object, max_length: int) -> str:
    text = str(value or "-").strip() or "-"
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3]}..."


def _build_review_notification_message(review_task: ReviewTask) -> str:
    trade_date = review_task.trade_date.isoformat() if review_task.trade_date else "-"
    required_by = _format_feishu_datetime(review_task.required_by)
    reason = (review_task.reason or "-").strip()
    if len(reason) > 80:
        reason = f"{reason[:77]}..."
    return (
        f"{_feishu_review_type_label(review_task.review_type)} | 业务日期={trade_date} | "
        f"对象={_feishu_scope_label(review_task.scope_type, review_task.scope_key)} | "
        f"截止时间={required_by} | 原因={reason}"
    )


def _build_expired_review_notification_message(review_task: ReviewTask, *, timeout_at: datetime) -> str:
    trade_date = review_task.trade_date.isoformat() if review_task.trade_date else "-"
    required_by = _format_feishu_datetime(review_task.required_by)
    return (
        f"{_feishu_review_type_label(review_task.review_type)} 已过期 | 业务日期={trade_date} | "
        f"对象={_feishu_scope_label(review_task.scope_type, review_task.scope_key)} | "
        f"截止时间={required_by} | 过期处理时间={_format_feishu_datetime(timeout_at)}"
    )

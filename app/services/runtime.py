from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import os
from typing import Protocol
from uuid import uuid4

from app.enums import NotificationSendStatus, ReviewTaskStatus, TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.models import ExecutionLog, NotificationLog, NotificationSendResult, ReviewTask, Task, TaskStatusHistory
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.execution import ExecutionSimulationService
from app.services.manual_intervention import MANUAL_INTERVENTION_ACTIONS
from app.utils import serialize_decimal, utc_now


DEFAULT_RUNTIME_DB = Path("data/runtime/pra_runtime.sqlite3")

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


class RuntimeTaskService:
    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository

    def init_schema(self) -> None:
        self.repository.init_schema()

    def create_tasks(self, tasks: list[Task], *, trade_date: date | None = None) -> int:
        normalized = [self._normalize_task(task, trade_date=trade_date) for task in tasks]
        return self.repository.insert_tasks(normalized)

    def list_tasks(
        self,
        *,
        trade_date: date | None = None,
        status: TaskStatus | None = None,
        action_type: TaskActionType | None = None,
    ) -> list[Task]:
        return self.repository.list_tasks(trade_date=trade_date, status=status, action_type=action_type)

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
        self.repository.update_task_status(task_id, to_status, result_message=result_message)
        self.repository.insert_status_history(
            TaskStatusHistory(
                history_id=uuid4().hex[:12],
                task_id=task_id,
                from_status=task.task_status,
                to_status=to_status,
                changed_by=changed_by,
                changed_at=datetime.now(),
                reason=reason,
                metadata=metadata or {},
            )
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
        notification_service: "ReviewNotificationService | None" = None,
    ) -> None:
        self.repository = repository
        self.runtime_task_service = runtime_task_service or RuntimeTaskService(repository)
        self.notification_service = notification_service or ReviewNotificationService(repository)

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
        self.repository.update_review_task(updated)
        if source_task_status is not None and review_task.source_task_id:
            source_task = self.runtime_task_service.get_task(review_task.source_task_id)
            if source_task is not None and source_task.task_status == TaskStatus.MANUAL_REVIEW:
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
                self.runtime_task_service.change_status(
                    task_id=review_task.source_task_id,
                    to_status=source_task_status,
                    changed_by=actor,
                    reason=f"review_task:{review_task_id}:{status.value}",
                    metadata=metadata,
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
            if enable_notification:
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
    ) -> list[NotificationLog]:
        return self.repository.list_notification_logs(
            related_review_task_id=related_review_task_id,
            send_status=send_status,
        )

    def get_log(self, notification_id: str) -> NotificationLog | None:
        return self.repository.get_notification_log(notification_id)


class NotificationSender(Protocol):
    channel: str

    def send(self, log: NotificationLog) -> NotificationSendResult:
        ...


class MockNotificationSender:
    channel = "mock"

    def send(self, log: NotificationLog) -> NotificationSendResult:
        return NotificationSendResult(
            send_status=NotificationSendStatus.SUCCESS.value,
            sent_at=datetime.now(),
            raw_response_json={"mock": True},
        )


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
        return self._create_notification(
            review_task,
            recipient_type=recipient_type,
            recipient=recipient,
            channel=channel,
            dedupe_suffix="initial",
            message=_build_review_notification_message(review_task),
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
        result = sender.send(log)
        log = replace(
            log,
            send_status=result.send_status,
            sent_at=result.sent_at,
            error_message=result.error_message,
        )
        inserted = self.notification_log_service.append(log)
        return log if inserted == 1 else None


class NotificationSenderFactory:
    def build(self, channel: str) -> NotificationSender:
        normalized = channel.strip().lower()
        if normalized == "mock":
            return MockNotificationSender()
        raise ValidationError(f"unsupported notification channel: {channel}")


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


def _build_review_notification_message(review_task: ReviewTask) -> str:
    trade_date = review_task.trade_date.isoformat() if review_task.trade_date else "-"
    required_by = review_task.required_by.isoformat() if review_task.required_by else "-"
    reason = (review_task.reason or "-").strip()
    if len(reason) > 80:
        reason = f"{reason[:77]}..."
    return (
        f"{review_task.review_type} | trade_date={trade_date} | "
        f"scope={review_task.scope_type}:{review_task.scope_key} | "
        f"required_by={required_by} | reason={reason}"
    )


def _build_expired_review_notification_message(review_task: ReviewTask, *, timeout_at: datetime) -> str:
    trade_date = review_task.trade_date.isoformat() if review_task.trade_date else "-"
    required_by = review_task.required_by.isoformat() if review_task.required_by else "-"
    return (
        f"{review_task.review_type} expired | trade_date={trade_date} | "
        f"scope={review_task.scope_type}:{review_task.scope_key} | "
        f"required_by={required_by} | timeout_at={timeout_at.isoformat()}"
    )

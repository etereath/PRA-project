from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.enums import ReviewTaskStatus, TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.models import ExecutionLog, NotificationLog, ReviewTask, Task, TaskStatusHistory
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
        status: TaskStatus | None = None,
        action_type: TaskActionType | None = None,
    ) -> list[Task]:
        return self.repository.list_tasks(status=status, action_type=action_type)

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
    ) -> None:
        self.repository = repository
        self.runtime_task_service = runtime_task_service or RuntimeTaskService(repository)

    def create_from_tasks(self, tasks: list[Task], *, trade_date: date | None = None) -> int:
        review_tasks = [
            _review_task_from_source(task, trade_date=trade_date)
            for task in tasks
            if task.action_type in MANUAL_INTERVENTION_ACTIONS
        ]
        return self.repository.insert_review_tasks(review_tasks)

    def list_review_tasks(self, *, status: ReviewTaskStatus | None = None) -> list[ReviewTask]:
        return self.repository.list_review_tasks(status=status)

    def resolve_review_task(
        self,
        *,
        review_task_id: str,
        status: ReviewTaskStatus,
        actor: str,
        note: str = "",
        resolution_payload: dict[str, object] | None = None,
        source_task_status: TaskStatus | None = None,
    ) -> ReviewTask:
        review_task = self.repository.get_review_task(review_task_id)
        if review_task is None:
            raise ValidationError(f"review task not found: {review_task_id}")
        self._validate_transition(review_task.review_status, status)
        now = datetime.now()
        updated = replace(
            review_task,
            review_status=status,
            resolution_payload=resolution_payload or {},
            updated_at=now,
            resolved_by=actor,
            resolved_at=now,
            resolution_note=note,
        )
        self.repository.update_review_task(updated)
        if source_task_status is not None and review_task.source_task_id:
            self.runtime_task_service.change_status(
                task_id=review_task.source_task_id,
                to_status=source_task_status,
                changed_by=actor,
                reason=f"review_task:{review_task_id}:{status.value}",
                metadata={"review_task_id": review_task_id, "review_status": status.value},
                result_message=note,
            )
        return updated

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

    def list_logs(self) -> list[NotificationLog]:
        return self.repository.list_notification_logs()


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

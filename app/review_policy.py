from __future__ import annotations

from datetime import datetime, timedelta

from app.enums import ReviewTaskStatus
from app.models import ReviewTask, Task
from app.services.manual_intervention import MANUAL_INTERVENTION_ACTIONS

RETRY_TASK_DECISION = "retry_task"
CANCEL_TASK_DECISION = "cancel_task"
RETRY_TASK_DEADLINE_MINUTES = 30
PRICE_EXECUTION_REVIEW_TYPE = "price_execution_unknown"

DEFAULT_REVIEW_STATUSES = (
    ReviewTaskStatus.APPROVED,
    ReviewTaskStatus.REJECTED,
    ReviewTaskStatus.ADJUSTED,
    ReviewTaskStatus.CANCELLED,
)
EXECUTION_FAILURE_REVIEW_STATUSES = (
    ReviewTaskStatus.APPROVED,
    ReviewTaskStatus.CANCELLED,
)


def retry_task_deadline(resolved_at: datetime) -> datetime:
    """Return the fresh execution deadline granted by a retry decision."""

    return resolved_at + timedelta(minutes=RETRY_TASK_DEADLINE_MINUTES)


def is_execution_failure_review(
    review_task: ReviewTask,
    source_task: Task | None = None,
) -> bool:
    """Return whether a review decides retry/cancel for a failed execution task."""

    if review_task.review_type != "manual_review":
        return False
    if review_task.review_payload.get("review_subject") == "task_group":
        return True
    action_type = (
        source_task.action_type.value
        if source_task is not None
        else str(review_task.review_payload.get("action_type") or "").strip()
    )
    if not action_type:
        return False
    return action_type not in {action.value for action in MANUAL_INTERVENTION_ACTIONS}


def task_group_id(task: Task) -> str:
    return str(task.decision_trace.get("task_group_id") or "").strip()


def review_task_group_id(review_task: ReviewTask) -> str:
    return str(
        review_task.review_payload.get("task_group_id")
        or (
            review_task.scope_key
            if review_task.scope_type == "task_group"
            else ""
        )
    ).strip()


def review_source_task_ids(review_task: ReviewTask) -> list[str]:
    raw_ids = review_task.review_payload.get("affected_task_ids")
    if not isinstance(raw_ids, list):
        raw_ids = review_task.review_payload.get("task_ids")
    resolved = []
    if isinstance(raw_ids, list):
        resolved = [
            str(task_id).strip()
            for task_id in raw_ids
            if str(task_id).strip()
        ]
    if not resolved and review_task.source_task_id:
        resolved = [review_task.source_task_id]
    return list(dict.fromkeys(resolved))


def allowed_review_statuses(
    review_task: ReviewTask,
    source_task: Task | None = None,
) -> tuple[ReviewTaskStatus, ...]:
    if review_task.review_type == PRICE_EXECUTION_REVIEW_TYPE:
        return ()
    if is_execution_failure_review(review_task, source_task):
        return EXECUTION_FAILURE_REVIEW_STATUSES
    return DEFAULT_REVIEW_STATUSES


def review_business_decision(
    review_task: ReviewTask,
    source_task: Task | None,
    status: ReviewTaskStatus,
) -> str | None:
    if not is_execution_failure_review(review_task, source_task):
        return None
    if status == ReviewTaskStatus.APPROVED:
        return RETRY_TASK_DECISION
    if status == ReviewTaskStatus.CANCELLED:
        return CANCEL_TASK_DECISION
    return None


def review_action_label(
    review_task: ReviewTask,
    source_task: Task | None,
    status: ReviewTaskStatus,
) -> str:
    if review_task.review_type == "emergency_protection":
        return {
            ReviewTaskStatus.ADJUSTED: "改价到",
            ReviewTaskStatus.APPROVED: "立即下架",
            ReviewTaskStatus.REJECTED: "我来处理",
        }.get(status, "")
    decision = review_business_decision(review_task, source_task, status)
    if decision == RETRY_TASK_DECISION:
        return "重试任务"
    if decision == CANCEL_TASK_DECISION:
        return "取消任务"
    return {
        ReviewTaskStatus.APPROVED: "通过",
        ReviewTaskStatus.REJECTED: "拒绝",
        ReviewTaskStatus.ADJUSTED: "调整",
        ReviewTaskStatus.CANCELLED: "取消",
    }.get(status, "")

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.enums import TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.models import Task


MANUAL_INTERVENTION_ACTIONS = {
    TaskActionType.CAPACITY_WARNING,
    TaskActionType.LABOR_REQUIRED,
    TaskActionType.MANUAL_PRICE_REVIEW,
    TaskActionType.BELOW_BREAK_EVEN_REVIEW,
    TaskActionType.SHORTAGE_WARNING,
    TaskActionType.COLD_STORAGE_WARNING,
    TaskActionType.CLEARANCE_WARNING,
    TaskActionType.MANUAL_REVIEW,
}

MANUAL_DECISIONS = {"acknowledge", "approve", "reject"}


class ManualInterventionService:
    def list_open_tasks(self, tasks: list[Task]) -> list[Task]:
        return [
            task
            for task in tasks
            if task.action_type in MANUAL_INTERVENTION_ACTIONS
            and task.task_status in {TaskStatus.PENDING, TaskStatus.MANUAL_REVIEW}
        ]

    def resolve_task(
        self,
        tasks: list[Task],
        *,
        task_id: str,
        decision: str,
        actor: str,
        note: str = "",
        resolved_at: datetime | None = None,
    ) -> list[Task]:
        normalized_decision = decision.strip().lower()
        if normalized_decision not in MANUAL_DECISIONS:
            supported = ", ".join(sorted(MANUAL_DECISIONS))
            raise ValidationError(f"manual decision must be one of: {supported}")

        timestamp = (resolved_at or datetime.now()).isoformat(timespec="seconds")
        status = {
            "acknowledge": TaskStatus.SKIPPED,
            "approve": TaskStatus.SUCCESS,
            "reject": TaskStatus.CANCELLED,
        }[normalized_decision]

        updated: list[Task] = []
        matched = False
        for task in tasks:
            if task.task_id != task_id:
                updated.append(task)
                continue
            if task.action_type not in MANUAL_INTERVENTION_ACTIONS:
                raise ValidationError(f"task {task_id} does not support manual intervention")
            matched = True
            manual_trace = dict(task.decision_trace)
            manual_trace["manual_intervention"] = {
                "decision": normalized_decision,
                "actor": actor,
                "note": note,
                "resolved_at": timestamp,
            }
            message = f"{normalized_decision} by {actor}".strip()
            if note.strip():
                message = f"{message}: {note.strip()}"
            updated.append(
                replace(
                    task,
                    task_status=status,
                    result_message=message,
                    decision_trace=manual_trace,
                )
            )
        if not matched:
            raise ValidationError(f"task {task_id} not found")
        return updated

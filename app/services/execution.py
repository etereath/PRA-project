from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from app.enums import TaskStatus
from app.models import ExecutionLog, Task
from app.utils import utc_now


class ExecutionSimulationService:
    def simulate(self, tasks: list[Task], executor_name: str = "mock_executor") -> tuple[list[Task], list[ExecutionLog]]:
        updated_tasks: list[Task] = []
        logs: list[ExecutionLog] = []

        for task in tasks:
            start_time = utc_now()
            end_time = utc_now()
            ai_model_version = ""
            ai_summary = ""
            if task.decision_trace:
                ai_model_version = str(task.decision_trace.get("ai_model_version") or "")
                ai_summary = str(task.decision_trace.get("ai_reason") or "")

            updated_tasks.append(
                replace(
                    task,
                    task_status=TaskStatus.SUCCESS,
                    result_message="simulated execution success",
                )
            )
            logs.append(
                ExecutionLog(
                    log_id=uuid4().hex[:12],
                    task_id=task.task_id,
                    executor_name=executor_name,
                    start_time=start_time,
                    end_time=end_time,
                    success_flag=True,
                    raw_output=f"simulated:{task.action_type.value}:{task.internal_sku}",
                    ai_model_version=ai_model_version,
                    ai_summary=ai_summary,
                )
            )

        return updated_tasks, logs

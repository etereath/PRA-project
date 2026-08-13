"""Thin 7E Automation handlers for Review timeout and daily task generation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from app.automation_models import AutomationRun, AutomationRunOutcome
from app.enums import AutomationRunStatus, TaskActionType
from app.repositories.automation_repository import AutomationRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.automation import (
    DAILY_TASK_GENERATION,
    REVIEW_TIMEOUT_MAINTENANCE,
    SALES_PLAN_INPUT_BUILD,
    AutomationExecutionContext,
    AutomationHandler,
)
from app.services.runtime import ReviewTaskService
from app.services.workflow import (
    WorkflowInputs,
    generate_tasks_from_sources,
    persist_task_generation_summary,
)


DAILY_TASK_SOURCES = frozenset({"PRODUCTS", "PRICE_RULES", "LISTING_RULES"})


@dataclass(frozen=True, slots=True)
class ReviewTimeoutAutomationHandler:
    runtime_repository: SQLiteRuntimeRepository

    def __call__(
        self,
        run: AutomationRun,
        context: AutomationExecutionContext,
    ) -> AutomationRunOutcome:
        if run.job_type != REVIEW_TIMEOUT_MAINTENANCE:
            raise ValueError("Review timeout handler received the wrong job type")
        if not context.heartbeat():
            raise RuntimeError("Automation lease was lost before Review timeout")
        summary = ReviewTaskService(self.runtime_repository).expire_pending_review_tasks(
            now=context.clock(),
            apply=True,
            actor="system:automation:review_timeout",
            enable_notification=True,
        )
        payload = {
            "schema_version": "review-timeout-automation-result-v1",
            "scanned_review_tasks": summary.scanned_review_tasks,
            "expired_review_tasks": summary.expired_review_tasks,
            "expired_source_tasks": summary.expired_source_tasks,
            "skipped_source_tasks": summary.skipped_source_tasks,
            "notification_logs_created": summary.notification_logs_created,
            "error_count": len(summary.errors),
            "platform_write_performed": False,
        }
        return AutomationRunOutcome(
            status=(
                AutomationRunStatus.PARTIAL
                if summary.errors
                else AutomationRunStatus.SUCCESS
            ),
            output_manifest_sha256=_manifest(payload),
            event_payload=payload,
        )


@dataclass(frozen=True, slots=True)
class DailyTaskGenerationAutomationHandler:
    runtime_repository: SQLiteRuntimeRepository
    automation_repository: AutomationRepository
    products_path: Path
    price_rules_path: Path
    listing_rules_path: Path

    def __call__(
        self,
        run: AutomationRun,
        context: AutomationExecutionContext,
    ) -> AutomationRunOutcome:
        if run.job_type != DAILY_TASK_GENERATION:
            raise ValueError("Daily task generation handler received the wrong job type")
        if not context.heartbeat():
            raise RuntimeError("Automation lease was lost before daily task generation")
        plan_input = self.automation_repository.latest_successful_run(
            job_type=SALES_PLAN_INPUT_BUILD,
            platform_name=run.platform_name,
            platform_trade_date=run.platform_trade_date,
        )
        if plan_input is None:
            return AutomationRunOutcome(
                status=AutomationRunStatus.SKIPPED,
                error_code="PLAN_INPUT_NOT_READY",
                error_message="同一 PRA 交易日的销售计划输入尚未成功",
                event_payload={
                    "platform_trade_date": run.platform_trade_date.isoformat(),
                    "platform_write_performed": False,
                },
            )
        job = self.automation_repository.get_job(run.job_id)
        if job is None:
            raise RuntimeError("Daily task generation job configuration is unavailable")
        sources = frozenset(str(value).strip().upper() for value in job.config.get("source_allowlist", ()))
        if (
            "PRODUCTS" not in sources
            or not sources.issubset(DAILY_TASK_SOURCES)
        ):
            raise ValueError("Daily task generation source_allowlist is invalid")

        paths = [self.products_path]
        if "PRICE_RULES" in sources:
            paths.append(self.price_rules_path)
        if "LISTING_RULES" in sources:
            paths.append(self.listing_rules_path)
        before = {path: path.read_bytes() for path in paths}
        input_payload = {
            "schema_version": "daily-task-generation-input-v1",
            "plan_input_run_id": plan_input.run_id,
            "plan_input_manifest_sha256": plan_input.output_manifest_sha256,
            "platform_trade_date": run.platform_trade_date.isoformat(),
            "seller_operation_date": run.seller_operation_date.isoformat(),
            "time_policy_version": run.time_policy_version,
            "source_allowlist": sorted(sources),
            "files": {
                path.name: "sha256:" + hashlib.sha256(content).hexdigest()
                for path, content in before.items()
            },
        }
        summary = generate_tasks_from_sources(
            WorkflowInputs(
                products_path=self.products_path,
                price_rules_path=self.price_rules_path,
                listing_rules_path=self.listing_rules_path,
                platform_name=run.platform_name,
                now=run.scheduled_for,
                runtime_db_path=self.runtime_repository.db_path,
                rule_source_allowlist=sources,
                origin_ref_id=run.run_id,
            )
        )
        after = {path: path.read_bytes() for path in paths}
        if after != before:
            raise RuntimeError("Daily task generation input changed during evaluation")
        allowed_actions = set()
        if "PRICE_RULES" in sources:
            allowed_actions.add(TaskActionType.UPDATE_PRICE)
        if "LISTING_RULES" in sources:
            allowed_actions.update(
                {TaskActionType.SET_ONLINE, TaskActionType.SET_OFFLINE}
            )
        filtered = replace(
            summary,
            tasks=[
                replace(
                    task,
                    platform_trade_date=run.platform_trade_date,
                    seller_operation_date=run.seller_operation_date,
                    time_policy_version=run.time_policy_version,
                )
                for task in summary.tasks
                if task.action_type in allowed_actions
            ],
        )
        input_manifest = _manifest(input_payload)
        context.bind_input_manifest(input_manifest)
        if not context.heartbeat():
            raise RuntimeError("Automation lease was lost before task persistence")
        stored = persist_task_generation_summary(
            filtered,
            db_path=self.runtime_repository.db_path,
            trade_date=run.platform_trade_date,
        )
        output_payload = {
            "schema_version": "daily-task-generation-result-v1",
            "input_manifest_sha256": input_manifest,
            "candidate_task_count": len(filtered.tasks),
            "inserted_task_count": stored.inserted_tasks_count,
            "inserted_review_task_count": stored.inserted_review_tasks_count,
            "notification_log_count": stored.inserted_notification_logs_count,
            "platform_trade_date": run.platform_trade_date.isoformat(),
            "seller_operation_date": run.seller_operation_date.isoformat(),
            "time_policy_version": run.time_policy_version,
            "source_allowlist": sorted(sources),
            "platform_write_performed": False,
        }
        return AutomationRunOutcome(
            status=AutomationRunStatus.SUCCESS,
            output_manifest_sha256=_manifest(output_payload),
            event_payload=output_payload,
        )


def build_operations_control_handlers(
    *,
    runtime_repository: SQLiteRuntimeRepository,
    products_path: Path,
    price_rules_path: Path,
    listing_rules_path: Path,
) -> Mapping[str, AutomationHandler]:
    automation = AutomationRepository(runtime_repository)
    return {
        REVIEW_TIMEOUT_MAINTENANCE: ReviewTimeoutAutomationHandler(
            runtime_repository
        ),
        DAILY_TASK_GENERATION: DailyTaskGenerationAutomationHandler(
            runtime_repository,
            automation,
            products_path,
            price_rules_path,
            listing_rules_path,
        ),
    }


def _manifest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()

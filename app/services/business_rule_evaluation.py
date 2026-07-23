from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.enums import TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.models import ColdStorageStatus, HarvestForecast, ListingRule, PackingCapacityPlan, Product, ScriptRun, ScriptRunItem, Task
from app.repositories.mock_platform_repository import DEFAULT_MOCK_PLATFORM_DB, MockPlatformRepository
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import (
    load_capacity_plans,
    load_cold_storage_statuses,
    load_harvest_forecasts,
    load_listing_rules,
    load_products,
)
from app.services.capacity_planning import CapacityPlanningService
from app.services.listing import ListingService
from app.services.runtime import ReviewTaskService, RuntimeTaskService
from app.utils import utc_now


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HARVEST_FORECASTS = ROOT / "data" / "samples" / "harvest_forecasts.xlsx"
DEFAULT_CAPACITY_PLANS = ROOT / "data" / "samples" / "capacity_plans.xlsx"
DEFAULT_PRODUCTS = ROOT / "data" / "samples" / "products.xlsx"
DEFAULT_LISTING_RULES = ROOT / "data" / "samples" / "listing_rules.xlsx"
DEFAULT_COLD_STORAGE_STATUS = ROOT / "data" / "samples" / "cold_storage_status.xlsx"

RUN_MODE_DRY_RUN = "dry-run"
RUN_MODE_APPLY = "apply"

RUN_STATUS_RUNNING = "running"
RUN_STATUS_SUCCESS = "success"
RUN_STATUS_FAILED = "failed"

ITEM_STATUS_PREVIEWED = "previewed"
ITEM_STATUS_APPLIED = "applied"
ITEM_STATUS_SKIPPED = "skipped"
ITEM_STATUS_FAILED = "failed"

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"
SEVERITY_CRITICAL = "critical"

PROPOSAL_RUNTIME_TASK = "runtime_task"
PROPOSAL_REVIEW_TASK = "review_task"
PROPOSAL_NOTIFICATION = "notification"
PROPOSAL_WARNING = "warning"
PROPOSAL_SKIPPED = "skipped"
PROPOSAL_ERROR = "error"


@dataclass(slots=True)
class EvaluationContext:
    trade_date: date
    runtime_db_path: Path
    run_mode: str = RUN_MODE_DRY_RUN
    now: datetime = field(default_factory=datetime.now)
    harvest_forecasts_path: Path = DEFAULT_HARVEST_FORECASTS
    capacity_plan_path: Path = DEFAULT_CAPACITY_PLANS
    products_path: Path = DEFAULT_PRODUCTS
    listing_rules_path: Path = DEFAULT_LISTING_RULES
    cold_storage_status_path: Path = DEFAULT_COLD_STORAGE_STATUS
    mock_platform_db_path: Path = DEFAULT_MOCK_PLATFORM_DB
    platform_name: str = "default_platform"
    created_by: str = "cli"
    harvest_forecasts: list[HarvestForecast] = field(default_factory=list)
    capacity_plan: PackingCapacityPlan | None = None
    products: list[Product] = field(default_factory=list)
    listing_rules: list[ListingRule] = field(default_factory=list)
    cold_storage_status: ColdStorageStatus | None = None
    mock_platform_states: list[object] = field(default_factory=list)


@dataclass(slots=True)
class Proposal:
    proposal_type: str
    dedupe_key: str
    severity: str
    message: str
    payload: dict[str, object] = field(default_factory=dict)
    decision_trace: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeTaskProposal(Proposal):
    pass


@dataclass(slots=True)
class ReviewTaskProposal(Proposal):
    pass


@dataclass(slots=True)
class NotificationProposal(Proposal):
    pass


@dataclass(slots=True)
class WarningProposal(Proposal):
    pass


@dataclass(slots=True)
class SkippedProposal(Proposal):
    pass


@dataclass(slots=True)
class ErrorProposal(Proposal):
    pass


class Evaluator(Protocol):
    evaluator_id: str
    evaluator_name: str
    description: str

    def evaluate(self, context: EvaluationContext) -> list[Proposal]:
        ...


@dataclass(slots=True)
class EvaluationRunSummary:
    script_run: ScriptRun
    items: list[ScriptRunItem]
    proposals_count: int
    inserted_tasks_count: int = 0
    inserted_review_tasks_count: int = 0
    inserted_notification_logs_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class CapacityRuleEvaluator:
    evaluator_id = "capacity_warning"
    evaluator_name = "包装产能预警"
    description = "比较预测产量与确认包装能力，生成产能预警和临时工确认复核建议。"

    def __init__(self, capacity_service: CapacityPlanningService | None = None) -> None:
        self.capacity_service = capacity_service or CapacityPlanningService()

    def evaluate(self, context: EvaluationContext) -> list[Proposal]:
        forecasts = context.harvest_forecasts
        plan = context.capacity_plan
        if not forecasts:
            return [
                SkippedProposal(
                    proposal_type=PROPOSAL_SKIPPED,
                    dedupe_key=f"{self.evaluator_id}|{context.trade_date.isoformat()}|no_harvest_forecasts",
                    severity=SEVERITY_WARNING,
                    message="没有产量预测数据，已跳过包装产能评估。",
                    payload={"trade_date": context.trade_date.isoformat()},
                    decision_trace={"skip_reason": "missing_harvest_forecasts"},
                )
            ]
        if plan is None:
            return [
                SkippedProposal(
                    proposal_type=PROPOSAL_SKIPPED,
                    dedupe_key=f"{self.evaluator_id}|{context.trade_date.isoformat()}|no_capacity_plan",
                    severity=SEVERITY_WARNING,
                    message="没有包装产能计划，已跳过包装产能评估。",
                    payload={"trade_date": context.trade_date.isoformat()},
                    decision_trace={"skip_reason": "missing_capacity_plan"},
                )
            ]

        review_requirements = self.capacity_service.build_capacity_reviews(forecasts, plan)
        predicted_total = self.capacity_service.predicted_total_harvest_qty(forecasts)
        if not review_requirements:
            return [
                SkippedProposal(
                    proposal_type=PROPOSAL_SKIPPED,
                    dedupe_key=f"{self.evaluator_id}|{context.trade_date.isoformat()}|capacity_ok",
                    severity=SEVERITY_INFO,
                    message="预测产量未超过确认包装能力，不需要生成产能复核。",
                    payload={
                        "trade_date": context.trade_date.isoformat(),
                        "predicted_total_harvest_qty": predicted_total,
                        "confirmed_packing_capacity_qty": plan.confirmed_packing_capacity_qty,
                    },
                    decision_trace={"skip_reason": "capacity_within_confirmed_limit"},
                )
            ]

        proposals: list[Proposal] = []
        for requirement in review_requirements:
            action_type = requirement.task_type.value
            dedupe_key = "|".join(
                [
                    "business_rule",
                    self.evaluator_id,
                    context.trade_date.isoformat(),
                    "global",
                    context.trade_date.isoformat(),
                    action_type,
                ]
            )
            details = dict(requirement.details)
            details["confirmed_packing_capacity_qty"] = plan.confirmed_packing_capacity_qty
            proposals.append(
                ReviewTaskProposal(
                    proposal_type=PROPOSAL_REVIEW_TASK,
                    dedupe_key=dedupe_key,
                    severity=SEVERITY_WARNING,
                    message=requirement.reason,
                    payload={
                        "trade_date": context.trade_date.isoformat(),
                        "scope_type": "global",
                        "scope_key": context.trade_date.isoformat(),
                        "action_type": action_type,
                        "reason": requirement.reason,
                        "required_by": requirement.required_by.isoformat() if requirement.required_by else None,
                        "details": details,
                    },
                    decision_trace={
                        "evaluator_id": self.evaluator_id,
                        "rule": "predicted_harvest_exceeds_confirmed_packing_capacity",
                        "data_source": "harvest_forecasts.predicted_harvest_qty",
                        "capacity_plan_trade_date": plan.trade_date.isoformat(),
                        "predicted_total_harvest_qty": predicted_total,
                        "normal_packing_capacity_qty": plan.normal_packing_capacity_qty,
                        "confirmed_temp_worker_count": plan.confirmed_temp_worker_count,
                        "confirmed_packing_capacity_qty": plan.confirmed_packing_capacity_qty,
                    },
                )
            )
        return proposals


class ListingRuleEvaluator:
    evaluator_id = "listing_rules"
    evaluator_name = "上下架规则评估"
    description = "根据商品库存、销售状态和上下架规则，生成需要人工确认的上下架建议。"

    def __init__(self, listing_service: ListingService | None = None) -> None:
        self.listing_service = listing_service or ListingService()

    def evaluate(self, context: EvaluationContext) -> list[Proposal]:
        if not context.products:
            return [
                SkippedProposal(
                    proposal_type=PROPOSAL_SKIPPED,
                    dedupe_key=f"{self.evaluator_id}|{context.trade_date.isoformat()}|no_products",
                    severity=SEVERITY_WARNING,
                    message="没有商品资料，已跳过上下架规则评估。",
                    payload={"trade_date": context.trade_date.isoformat()},
                    decision_trace={"skip_reason": "missing_products"},
                )
            ]
        if not context.listing_rules:
            return [
                SkippedProposal(
                    proposal_type=PROPOSAL_SKIPPED,
                    dedupe_key=f"{self.evaluator_id}|{context.trade_date.isoformat()}|no_listing_rules",
                    severity=SEVERITY_WARNING,
                    message="没有上下架规则，已跳过上下架规则评估。",
                    payload={"trade_date": context.trade_date.isoformat()},
                    decision_trace={"skip_reason": "missing_listing_rules"},
                )
            ]

        proposals: list[Proposal] = []
        for product in context.products:
            action, trace = self.listing_service.evaluate(product, context.listing_rules, context.platform_name)
            if action != TaskActionType.SET_OFFLINE.value:
                continue
            reason = (
                f"上下架规则建议下架：{product.product_name} {product.grade}，"
                f"当前库存 {product.current_stock}。"
            )
            dedupe_key = "|".join(
                [
                    "business_rule",
                    self.evaluator_id,
                    context.trade_date.isoformat(),
                    "sku",
                    product.internal_sku,
                    TaskActionType.MANUAL_REVIEW.value,
                    context.platform_name,
                ]
            )
            proposals.append(
                ReviewTaskProposal(
                    proposal_type=PROPOSAL_REVIEW_TASK,
                    dedupe_key=dedupe_key,
                    severity=SEVERITY_WARNING,
                    message=reason,
                    payload={
                        "trade_date": context.trade_date.isoformat(),
                        "scope_type": "sku",
                        "scope_key": product.internal_sku,
                        "internal_sku": product.internal_sku,
                        "platform_name": context.platform_name,
                        "action_type": TaskActionType.MANUAL_REVIEW.value,
                        "target_status": TaskActionType.SET_OFFLINE.value,
                        "reason": reason,
                        "details": {
                            "product_name": product.product_name,
                            "grade": product.grade,
                            "current_stock": product.current_stock,
                            "sale_enabled": product.sale_enabled,
                        },
                    },
                    decision_trace={
                        "evaluator_id": self.evaluator_id,
                        "rule": "listing_rule_requires_manual_review_before_platform_action",
                        "proposed_action": TaskActionType.SET_OFFLINE.value,
                        "listing_trace": trace,
                        "platform_name": context.platform_name,
                    },
                )
            )
        if not proposals:
            return [
                SkippedProposal(
                    proposal_type=PROPOSAL_SKIPPED,
                    dedupe_key=f"{self.evaluator_id}|{context.trade_date.isoformat()}|no_listing_review_needed",
                    severity=SEVERITY_INFO,
                    message="上下架规则未发现需要人工确认的下架建议。",
                    payload={"trade_date": context.trade_date.isoformat(), "platform_name": context.platform_name},
                    decision_trace={"skip_reason": "no_listing_review_needed"},
                )
            ]
        return proposals


class ColdStorageEvaluator:
    evaluator_id = "cold_storage"
    evaluator_name = "冷库容量预警"
    description = "比较预计冷库占用量与冷库容量，生成冷库容量预警复核建议。"

    def evaluate(self, context: EvaluationContext) -> list[Proposal]:
        status = context.cold_storage_status
        if status is None:
            return [
                SkippedProposal(
                    proposal_type=PROPOSAL_SKIPPED,
                    dedupe_key=f"{self.evaluator_id}|{context.trade_date.isoformat()}|no_cold_storage_status",
                    severity=SEVERITY_WARNING,
                    message="没有冷库状态数据，已跳过冷库容量评估。",
                    payload={"trade_date": context.trade_date.isoformat()},
                    decision_trace={"skip_reason": "missing_cold_storage_status"},
                )
            ]

        projected = status.projected_occupied_qty
        remaining = status.remaining_capacity_qty
        total_capacity = status.total_capacity_qty
        threshold = status.warning_threshold_qty
        base_payload = {
            "trade_date": context.trade_date.isoformat(),
            "scope_type": "global",
            "scope_key": context.trade_date.isoformat(),
            "action_type": TaskActionType.COLD_STORAGE_WARNING.value,
            "details": {
                "total_capacity_qty": total_capacity,
                "current_occupied_qty": status.current_occupied_qty,
                "expected_inbound_qty": status.expected_inbound_qty,
                "expected_outbound_qty": status.expected_outbound_qty,
                "projected_occupied_qty": projected,
                "remaining_capacity_qty": remaining,
                "warning_threshold_qty": threshold,
            },
        }
        base_trace = {
            "evaluator_id": self.evaluator_id,
            "data_source": "cold_storage_status.xlsx",
            "cold_storage_trade_date": status.trade_date.isoformat(),
            "total_capacity_qty": total_capacity,
            "projected_occupied_qty": projected,
            "remaining_capacity_qty": remaining,
            "warning_threshold_qty": threshold,
        }
        if projected > total_capacity:
            reason = "冷库预计占用量超过总容量，需要人工复核处理。"
            event_type = "cold_storage_capacity_exceeded"
            return [
                ReviewTaskProposal(
                    proposal_type=PROPOSAL_REVIEW_TASK,
                    dedupe_key=_cold_storage_dedupe_key(self.evaluator_id, context.trade_date, event_type),
                    severity=SEVERITY_CRITICAL,
                    message=reason,
                    payload=base_payload
                    | {
                        "reason": reason,
                        "event_type": event_type,
                    },
                    decision_trace=base_trace
                    | {
                        "rule": "projected_occupied_exceeds_total_capacity",
                        "event_type": event_type,
                    },
                )
            ]
        if remaining <= threshold:
            reason = "冷库剩余容量已低于或等于预警阈值，需要人工确认。"
            event_type = "cold_storage_low_remaining_capacity"
            return [
                ReviewTaskProposal(
                    proposal_type=PROPOSAL_REVIEW_TASK,
                    dedupe_key=_cold_storage_dedupe_key(self.evaluator_id, context.trade_date, event_type),
                    severity=SEVERITY_WARNING,
                    message=reason,
                    payload=base_payload
                    | {
                        "reason": reason,
                        "event_type": event_type,
                    },
                    decision_trace=base_trace
                    | {
                        "rule": "remaining_capacity_lte_warning_threshold",
                        "event_type": event_type,
                    },
                )
            ]
        return [
            SkippedProposal(
                proposal_type=PROPOSAL_SKIPPED,
                dedupe_key=f"{self.evaluator_id}|{context.trade_date.isoformat()}|cold_storage_ok",
                severity=SEVERITY_INFO,
                message="冷库预计占用量未超过容量，剩余容量也未低于预警阈值。",
                payload=base_payload,
                decision_trace=base_trace | {"skip_reason": "cold_storage_within_limit"},
            )
        ]


class PlatformSyncEvaluator:
    evaluator_id = "platform_sync"
    evaluator_name = "模拟平台同步差异检查"
    description = "读取 Mock Platform 状态，对比 PRA 任务预期与平台实际，生成价格、上架状态和库存差异复核建议。"

    def evaluate(self, context: EvaluationContext) -> list[Proposal]:
        tasks = [
            task
            for task in RuntimeTaskService(SQLiteRuntimeRepository(context.runtime_db_path)).list_tasks()
            if task.platform_name
            and task.platform_name == context.platform_name
            and task.action_type
            in {TaskActionType.UPDATE_PRICE, TaskActionType.SET_ONLINE, TaskActionType.SET_OFFLINE, TaskActionType.SYNC_STATUS}
        ]
        if not tasks:
            return [
                SkippedProposal(
                    proposal_type=PROPOSAL_SKIPPED,
                    dedupe_key=f"{self.evaluator_id}|{context.trade_date.isoformat()}|no_platform_tasks",
                    severity=SEVERITY_INFO,
                    message="没有可用于模拟平台同步对比的运行态任务。",
                    payload={"trade_date": context.trade_date.isoformat(), "platform_name": context.platform_name},
                    decision_trace={"skip_reason": "no_platform_tasks"},
                )
            ]
        state_by_key = {
            (state.platform_name, state.internal_sku): state
            for state in context.mock_platform_states
        }
        proposals: list[Proposal] = []
        for task in tasks:
            state = state_by_key.get((task.platform_name or "", task.internal_sku or ""))
            if state is None:
                proposals.append(
                    _platform_sync_proposal(
                        evaluator_id=self.evaluator_id,
                        trade_date=context.trade_date,
                        task=task,
                        mismatch_type="platform_sync_warning",
                        severity=SEVERITY_WARNING,
                        message="模拟平台商品不存在，需要人工确认平台映射或商品状态。",
                        expected={},
                        actual={},
                        decision_trace={"rule": "mock_platform_product_missing"},
                    )
                )
                continue
            if task.action_type == TaskActionType.UPDATE_PRICE and task.target_price is not None:
                expected_price = str(task.target_price)
                actual_price = str(state.platform_price) if state.platform_price is not None else ""
                if not _same_decimal_value(expected_price, actual_price):
                    proposals.append(
                        _platform_sync_proposal(
                            evaluator_id=self.evaluator_id,
                            trade_date=context.trade_date,
                            task=task,
                            mismatch_type="price_mismatch",
                            severity=SEVERITY_WARNING,
                            message="模拟平台价格与 PRA 目标价格不一致，需要人工复核。",
                            expected={"target_price": expected_price},
                            actual={"platform_price": actual_price},
                            decision_trace={"rule": "target_price_ne_platform_price"},
                        )
                    )
            expected_status = _expected_platform_status(task)
            if expected_status and state.platform_online_status != expected_status:
                proposals.append(
                    _platform_sync_proposal(
                        evaluator_id=self.evaluator_id,
                        trade_date=context.trade_date,
                        task=task,
                        mismatch_type="listing_status_mismatch",
                        severity=SEVERITY_WARNING,
                        message="模拟平台上下架状态与 PRA 预期不一致，需要人工复核。",
                        expected={"target_status": expected_status},
                        actual={"platform_online_status": state.platform_online_status},
                        decision_trace={"rule": "target_status_ne_platform_online_status"},
                    )
                )
            if state.platform_stock_qty <= 0:
                proposals.append(
                    _platform_sync_proposal(
                        evaluator_id=self.evaluator_id,
                        trade_date=context.trade_date,
                        task=task,
                        mismatch_type="stock_mismatch",
                        severity=SEVERITY_WARNING,
                        message="模拟平台库存为 0，需要人工确认是否下架或同步库存。",
                        expected={"stock_policy": "platform_stock_should_be_positive_for_online_candidate"},
                        actual={"platform_stock_qty": state.platform_stock_qty},
                        decision_trace={"rule": "platform_stock_lte_zero"},
                    )
                )
        if not proposals:
            return [
                SkippedProposal(
                    proposal_type=PROPOSAL_SKIPPED,
                    dedupe_key=f"{self.evaluator_id}|{context.trade_date.isoformat()}|platform_state_matched",
                    severity=SEVERITY_INFO,
                    message="模拟平台状态与 PRA 当前预期一致，未发现需要复核的差异。",
                    payload={"trade_date": context.trade_date.isoformat(), "platform_name": context.platform_name},
                    decision_trace={"skip_reason": "platform_state_matched"},
                )
            ]
        return proposals


class BusinessRuleRunner:
    def __init__(self, repository: SQLiteRuntimeRepository, evaluators: dict[str, Evaluator] | None = None) -> None:
        self.repository = repository
        self.evaluators = evaluators or default_evaluators()

    def list_evaluators(self) -> list[Evaluator]:
        return sorted(self.evaluators.values(), key=lambda item: item.evaluator_id)

    def run(self, evaluator_id: str, context: EvaluationContext) -> EvaluationRunSummary:
        if context.run_mode not in {RUN_MODE_DRY_RUN, RUN_MODE_APPLY}:
            raise ValidationError("run_mode must be dry-run or apply")
        evaluator = self.evaluators.get(evaluator_id)
        if evaluator is None:
            raise ValidationError(f"unknown evaluator: {evaluator_id}")

        self.repository.init_schema()
        context = self._load_context_inputs(context)
        script_run = ScriptRun(
            script_run_id=self._script_run_id(evaluator_id, context),
            evaluator_id=evaluator.evaluator_id,
            evaluator_name=evaluator.evaluator_name,
            description=evaluator.description,
            run_mode=context.run_mode,
            run_status=RUN_STATUS_RUNNING,
            trade_date=context.trade_date,
            started_at=context.now,
            created_by=context.created_by,
        )
        self.repository.insert_script_run(script_run)
        items: list[ScriptRunItem] = []
        inserted_tasks_count = 0
        inserted_review_tasks_count = 0
        inserted_notification_logs_count = 0
        warnings: list[str] = []
        errors: list[str] = []

        try:
            proposals = evaluator.evaluate(context)
            for proposal in proposals:
                item = self._proposal_to_item(script_run.script_run_id, proposal, context)
                if not proposal.dedupe_key:
                    item.item_status = ITEM_STATUS_FAILED
                    item.error_message = "proposal dedupe_key is required"
                    errors.append(item.error_message)
                elif context.run_mode == RUN_MODE_DRY_RUN:
                    item.item_status = ITEM_STATUS_PREVIEWED
                else:
                    applied = self._apply_proposal(proposal, item, context)
                    inserted_tasks_count += applied["tasks"]
                    inserted_review_tasks_count += applied["reviews"]
                    inserted_notification_logs_count += applied["notifications"]
                    notification_errors = item.decision_trace.get("notification_errors")
                    if notification_errors:
                        warnings.append(f"{item.message}: notification_errors={notification_errors}")
                if item.severity in {SEVERITY_WARNING, SEVERITY_CRITICAL}:
                    warnings.append(item.message)
                if item.item_status == ITEM_STATUS_FAILED and item.error_message:
                    errors.append(item.error_message)
                items.append(item)
            self.repository.insert_script_run_items(items)
            finished = datetime.now()
            final_status = RUN_STATUS_FAILED if errors else RUN_STATUS_SUCCESS
            script_run = ScriptRun(
                script_run_id=script_run.script_run_id,
                evaluator_id=script_run.evaluator_id,
                evaluator_name=script_run.evaluator_name,
                description=script_run.description,
                run_mode=script_run.run_mode,
                run_status=final_status,
                trade_date=script_run.trade_date,
                started_at=script_run.started_at,
                finished_at=finished,
                summary={
                    "proposals_count": len(proposals),
                    "inserted_tasks_count": inserted_tasks_count,
                    "inserted_review_tasks_count": inserted_review_tasks_count,
                    "inserted_notification_logs_count": inserted_notification_logs_count,
                    "warnings_count": len(warnings),
                    "errors_count": len(errors),
                },
                error_message="; ".join(errors[:3]),
                created_by=script_run.created_by,
            )
            self.repository.update_script_run(script_run)
            return EvaluationRunSummary(
                script_run=script_run,
                items=items,
                proposals_count=len(proposals),
                inserted_tasks_count=inserted_tasks_count,
                inserted_review_tasks_count=inserted_review_tasks_count,
                inserted_notification_logs_count=inserted_notification_logs_count,
                warnings=warnings,
                errors=errors,
            )
        except Exception as exc:
            error = _safe_error(exc)
            failed = ScriptRun(
                script_run_id=script_run.script_run_id,
                evaluator_id=script_run.evaluator_id,
                evaluator_name=script_run.evaluator_name,
                description=script_run.description,
                run_mode=script_run.run_mode,
                run_status=RUN_STATUS_FAILED,
                trade_date=script_run.trade_date,
                started_at=script_run.started_at,
                finished_at=datetime.now(),
                summary={"proposals_count": len(items), "errors_count": 1},
                error_message=error,
                created_by=script_run.created_by,
            )
            self.repository.update_script_run(failed)
            return EvaluationRunSummary(
                script_run=failed,
                items=items,
                proposals_count=len(items),
                errors=[error],
            )

    def _load_context_inputs(self, context: EvaluationContext) -> EvaluationContext:
        forecasts: list[HarvestForecast] = []
        capacity_plan: PackingCapacityPlan | None = None
        products: list[Product] = []
        listing_rules: list[ListingRule] = []
        cold_storage_status: ColdStorageStatus | None = None
        mock_platform_states: list[object] = []
        if context.harvest_forecasts_path.exists():
            forecasts = [
                forecast
                for forecast in load_harvest_forecasts(context.harvest_forecasts_path)
                if forecast.target_trade_date == context.trade_date
            ]
        if context.capacity_plan_path.exists():
            try:
                plans = load_capacity_plans(context.capacity_plan_path)
            except ValidationError as exc:
                if "requires at least one row" not in str(exc):
                    raise
                plans = []
            active_plans = [plan for plan in plans if plan.trade_date == context.trade_date and plan.active]
            if len(active_plans) == 1:
                capacity_plan = active_plans[0]
        if context.products_path.exists():
            products = load_products(context.products_path)
        if context.listing_rules_path.exists():
            listing_rules = load_listing_rules(context.listing_rules_path)
        if context.cold_storage_status_path.exists():
            try:
                statuses = load_cold_storage_statuses(context.cold_storage_status_path)
            except ValidationError as exc:
                if "requires at least one row" not in str(exc):
                    raise
                statuses = []
            active_statuses = [
                status for status in statuses if status.trade_date == context.trade_date and status.active
            ]
            if len(active_statuses) == 1:
                cold_storage_status = active_statuses[0]
        if context.mock_platform_db_path.exists():
            mock_platform_states = MockPlatformRepository(context.mock_platform_db_path).list_product_states(
                platform_name=context.platform_name
            )
        return EvaluationContext(
            trade_date=context.trade_date,
            runtime_db_path=context.runtime_db_path,
            run_mode=context.run_mode,
            now=context.now,
            harvest_forecasts_path=context.harvest_forecasts_path,
            capacity_plan_path=context.capacity_plan_path,
            products_path=context.products_path,
            listing_rules_path=context.listing_rules_path,
            cold_storage_status_path=context.cold_storage_status_path,
            mock_platform_db_path=context.mock_platform_db_path,
            platform_name=context.platform_name,
            created_by=context.created_by,
            harvest_forecasts=forecasts,
            capacity_plan=capacity_plan,
            products=products,
            listing_rules=listing_rules,
            cold_storage_status=cold_storage_status,
            mock_platform_states=mock_platform_states,
        )

    def _apply_proposal(
        self,
        proposal: Proposal,
        item: ScriptRunItem,
        context: EvaluationContext,
    ) -> dict[str, int]:
        counts = {"tasks": 0, "reviews": 0, "notifications": 0}
        if proposal.proposal_type != PROPOSAL_REVIEW_TASK:
            item.item_status = ITEM_STATUS_SKIPPED
            item.decision_trace["skip_reason"] = "proposal_type_not_applyable_in_mvp"
            return counts

        existing_task = self.repository.get_open_task_by_dedupe_key(proposal.dedupe_key)
        existing_review = self.repository.get_pending_review_task_by_dedupe_key("review|" + proposal.dedupe_key)
        if existing_task is not None or existing_review is not None:
            item.item_status = ITEM_STATUS_SKIPPED
            item.decision_trace["skip_reason"] = "dedupe_key_already_applied"
            if existing_task is not None:
                item.related_task_id = existing_task.task_id
                item.payload["existing_task_id"] = existing_task.task_id
            if existing_review is not None:
                item.related_review_task_id = existing_review.review_task_id
                item.payload["existing_review_task_id"] = existing_review.review_task_id
            item.message = f"{item.message}；已存在相同待处理事项，跳过重复生成。"
            return counts

        task = _task_from_review_proposal(proposal, context)
        runtime_task_service = RuntimeTaskService(self.repository)
        inserted_tasks = runtime_task_service.create_tasks([task], trade_date=context.trade_date)
        if inserted_tasks != 1:
            item.item_status = ITEM_STATUS_SKIPPED
            item.decision_trace["skip_reason"] = "task_dedupe_insert_ignored"
            return counts
        counts["tasks"] = 1
        item.related_task_id = task.task_id
        review_summary = ReviewTaskService(
            self.repository,
            runtime_task_service=runtime_task_service,
        ).create_from_tasks([task], trade_date=context.trade_date)
        counts["reviews"] = review_summary.inserted_review_tasks_count
        counts["notifications"] = review_summary.inserted_notification_logs_count
        if review_summary.review_tasks:
            item.related_review_task_id = review_summary.review_tasks[0].review_task_id
            notifications = self.repository.list_notification_logs(
                related_review_task_id=item.related_review_task_id
            )
            if notifications:
                item.related_notification_id = notifications[0].notification_id
        item.item_status = ITEM_STATUS_APPLIED
        if review_summary.notification_errors:
            item.decision_trace["notification_errors"] = review_summary.notification_errors
            item.error_message = "; ".join(review_summary.notification_errors[:3])
        return counts

    def _proposal_to_item(self, script_run_id: str, proposal: Proposal, context: EvaluationContext) -> ScriptRunItem:
        return ScriptRunItem(
            item_id=uuid4().hex[:12],
            script_run_id=script_run_id,
            proposal_type=proposal.proposal_type,
            dedupe_key=proposal.dedupe_key,
            severity=proposal.severity,
            item_status=ITEM_STATUS_PREVIEWED if context.run_mode == RUN_MODE_DRY_RUN else ITEM_STATUS_SKIPPED,
            message=proposal.message,
            payload=dict(proposal.payload),
            decision_trace=dict(proposal.decision_trace),
            created_at=datetime.now(),
        )

    def _script_run_id(self, evaluator_id: str, context: EvaluationContext) -> str:
        stamp = context.now.strftime("%Y%m%d%H%M%S")
        return f"{evaluator_id}-{context.trade_date.isoformat()}-{stamp}-{uuid4().hex[:6]}"


def default_evaluators() -> dict[str, Evaluator]:
    evaluators: list[Evaluator] = [
        CapacityRuleEvaluator(),
        ListingRuleEvaluator(),
        ColdStorageEvaluator(),
        PlatformSyncEvaluator(),
    ]
    return {evaluator.evaluator_id: evaluator for evaluator in evaluators}


def _cold_storage_dedupe_key(evaluator_id: str, trade_date: date, event_type: str) -> str:
    return "|".join(
        [
            "business_rule",
            evaluator_id,
            trade_date.isoformat(),
            "global",
            trade_date.isoformat(),
            event_type,
        ]
    )


def _expected_platform_status(task: Task) -> str:
    if task.action_type == TaskActionType.SET_ONLINE:
        return "online"
    if task.action_type == TaskActionType.SET_OFFLINE:
        return "offline"
    if task.target_status == TaskActionType.SET_ONLINE.value:
        return "online"
    if task.target_status == TaskActionType.SET_OFFLINE.value:
        return "offline"
    return ""


def _same_decimal_value(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return str(left) == str(right)


def _platform_sync_proposal(
    *,
    evaluator_id: str,
    trade_date: date,
    task: Task,
    mismatch_type: str,
    severity: str,
    message: str,
    expected: dict[str, object],
    actual: dict[str, object],
    decision_trace: dict[str, object],
) -> ReviewTaskProposal:
    platform_name = task.platform_name or "default_platform"
    internal_sku = task.internal_sku or ""
    dedupe_key = "|".join(
        [
            "business_rule",
            evaluator_id,
            trade_date.isoformat(),
            "sku",
            internal_sku,
            platform_name,
            mismatch_type,
        ]
    )
    return ReviewTaskProposal(
        proposal_type=PROPOSAL_REVIEW_TASK,
        dedupe_key=dedupe_key,
        severity=severity,
        message=message,
        payload={
            "trade_date": trade_date.isoformat(),
            "scope_type": "sku",
            "scope_key": internal_sku,
            "internal_sku": internal_sku,
            "platform_name": platform_name,
            "action_type": TaskActionType.MANUAL_REVIEW.value,
            "reason": message,
            "mismatch_type": mismatch_type,
            "source_task_id": task.task_id,
            "details": {
                "expected": expected,
                "actual": actual,
                "task_action_type": task.action_type.value,
            },
        },
        decision_trace={
            "evaluator_id": evaluator_id,
            "source": "mock_platform.sqlite3",
            "mismatch_type": mismatch_type,
            "source_task_id": task.task_id,
        }
        | decision_trace,
    )


def _task_from_review_proposal(proposal: Proposal, context: EvaluationContext) -> Task:
    payload = proposal.payload
    action_type = TaskActionType(str(payload.get("action_type") or TaskActionType.CAPACITY_WARNING.value))
    required_by_raw = payload.get("required_by")
    required_by = datetime.fromisoformat(str(required_by_raw)) if required_by_raw else None
    internal_sku = str(payload.get("internal_sku") or "__operation__")
    platform_name = str(payload.get("platform_name") or "") or None
    return Task(
        task_id=uuid4().hex[:12],
        internal_sku=internal_sku,
        platform_name=platform_name,
        action_type=action_type,
        priority=2,
        task_status=TaskStatus.PENDING,
        created_at=utc_now(),
        target_status=str(payload.get("target_status") or "") or None,
        result_message=str(payload.get("reason") or proposal.message),
        required_by=required_by,
        trade_date=context.trade_date,
        scope_type=str(payload.get("scope_type") or "global"),
        scope_key=str(payload.get("scope_key") or context.trade_date.isoformat()),
        dedupe_key=proposal.dedupe_key,
        decision_trace=proposal.decision_trace | {"proposal_payload": proposal.payload},
    )


def _safe_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    for marker in ["token=", "FEISHU_WEBHOOK_URL", "REVIEW_TOKEN_SECRET", "RUNTIME_ADMIN_PASSWORD"]:
        text = text.replace(marker, "[redacted]")
    return text[:500]

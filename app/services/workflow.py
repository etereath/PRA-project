from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.enums import ReviewTaskStatus, TaskActionType, TaskStatus
from app.exceptions import MobileReviewErrorCode, MobileReviewTransactionError, ValidationError
from app.listing_identity import listing_identity_key
from app.listing_status_policy import is_price_task_listing
from app.models import (
    ColdStorageStatus,
    ExecutionLog,
    HarvestForecast,
    NotificationLog,
    PackingCapacityPlan,
    PriceForecast,
    Product,
    ReviewTask,
    ReviewToken,
    Task,
    TaskStatusHistory,
)
from app.platform_identity import canonical_platform_name, platform_identity_key, platform_names_match
from app.repositories.workbook_repository import (
    export_execution_logs,
    export_tasks,
    load_capacity_plan,
    load_cold_storage_status,
    load_harvest_forecasts,
    load_listing_rule,
    load_listing_rules,
    load_price_forecasts,
    load_price_rule,
    load_price_rules,
    load_products,
    load_tasks,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.ai import MockAISuggestionProvider, NullAISuggestionProvider
from app.services.execution import ExecutionSimulationService
from app.services.listing import ListingService
from app.services.manual_intervention import MANUAL_INTERVENTION_ACTIONS, ManualInterventionService
from app.services.pricing import PricingService
from app.services.runtime import (
    DEFAULT_RUNTIME_DB,
    ExpireReviewTasksSummary,
    NotificationLogService,
    ReviewTaskService,
    ReviewTokenService,
    RuntimeTaskService,
)
from app.services.task_generation import TaskGenerationService


@dataclass(slots=True)
class WorkflowInputs:
    products_path: Path
    price_rules_path: Path
    listing_rules_path: Path
    output_path: Path | None = None
    platform_name: str = "default_platform"
    use_mock_ai: bool = False
    harvest_forecasts_path: Path | None = None
    price_forecasts_path: Path | None = None
    capacity_plan_path: Path | None = None
    cold_storage_status_path: Path | None = None
    trade_date: date | None = None
    now: datetime | None = None
    inventory_strategy: str = "conservative_v1"
    runtime_db_path: Path | None = None
    platform_names: tuple[str, ...] = ()


@dataclass(slots=True)
class ValidationSummary:
    products: list[Product]
    price_rules_count: int
    listing_rules_count: int
    harvest_forecasts: list[HarvestForecast] | None = None
    price_forecasts: list[PriceForecast] | None = None
    capacity_plan: PackingCapacityPlan | None = None
    cold_storage_status: ColdStorageStatus | None = None

    @property
    def harvest_forecasts_count(self) -> int:
        return len(self.harvest_forecasts or [])

    @property
    def price_forecasts_count(self) -> int:
        return len(self.price_forecasts or [])


@dataclass(slots=True)
class TaskGenerationSummary:
    validation: ValidationSummary
    tasks: list[Task]
    output_path: Path | None
    output_written: bool = False

    @property
    def task_counts(self) -> dict[str, int]:
        return dict(Counter(task.action_type.value for task in self.tasks))


@dataclass(slots=True)
class RuntimeDatabaseInputs:
    db_path: Path = DEFAULT_RUNTIME_DB


@dataclass(slots=True)
class RuntimeTaskGenerationSummary:
    validation: ValidationSummary
    tasks: list[Task]
    inserted_tasks_count: int
    inserted_review_tasks_count: int
    inserted_notification_logs_count: int
    db_path: Path
    notification_errors: list[str] | None = None

    @property
    def task_counts(self) -> dict[str, int]:
        return dict(Counter(task.action_type.value for task in self.tasks))


@dataclass(slots=True)
class RuntimeReviewResolutionInputs:
    db_path: Path
    review_task_id: str
    status: ReviewTaskStatus
    actor: str = "manual_operator"
    actor_source: str = "manual_operator"
    note: str = ""
    source_task_status: TaskStatus | None = None
    resolution_payload: dict[str, object] | None = None


@dataclass(slots=True)
class MobileReviewDetail:
    review_task: ReviewTask
    review_token: ReviewToken
    source_task: Task | None
    allowed_actions: list[str]


@dataclass(slots=True)
class MobileReviewResolutionSummary:
    review_task: ReviewTask
    review_token: ReviewToken
    source_task: Task | None
    source_task_status: TaskStatus | None


@dataclass(slots=True)
class ExpireReviewTasksInputs:
    db_path: Path
    apply: bool = False
    now: datetime | None = None
    enable_notification: bool = False


@dataclass(slots=True)
class ExecutionSimulationInputs:
    tasks_path: Path
    logs_output_path: Path
    updated_tasks_output_path: Path | None = None
    executor_name: str = "mock_executor"


@dataclass(slots=True)
class ExecutionSimulationSummary:
    source_tasks_path: Path
    logs_output_path: Path
    updated_tasks_output_path: Path | None
    tasks: list[Task]
    logs: list[ExecutionLog]

    @property
    def success_count(self) -> int:
        return sum(1 for log in self.logs if log.success_flag)


@dataclass(slots=True)
class ManualInterventionInputs:
    tasks_path: Path
    output_path: Path
    task_id: str
    decision: str
    actor: str = "manual_operator"
    note: str = ""


@dataclass(slots=True)
class ManualInterventionSummary:
    source_tasks_path: Path
    output_path: Path
    open_tasks: list[Task]
    updated_task: Task


def init_runtime_database(inputs: RuntimeDatabaseInputs) -> list[int]:
    repository = SQLiteRuntimeRepository(inputs.db_path)
    RuntimeTaskService(repository).init_schema()
    return repository.schema_versions()


def generate_runtime_tasks_from_sources(inputs: WorkflowInputs, *, db_path: Path = DEFAULT_RUNTIME_DB) -> RuntimeTaskGenerationSummary:
    preview_inputs = WorkflowInputs(
        products_path=inputs.products_path,
        price_rules_path=inputs.price_rules_path,
        listing_rules_path=inputs.listing_rules_path,
        output_path=None,
        platform_name=inputs.platform_name,
        use_mock_ai=inputs.use_mock_ai,
        harvest_forecasts_path=inputs.harvest_forecasts_path,
        price_forecasts_path=inputs.price_forecasts_path,
        capacity_plan_path=inputs.capacity_plan_path,
        cold_storage_status_path=inputs.cold_storage_status_path,
        trade_date=inputs.trade_date,
        now=inputs.now,
        inventory_strategy=inputs.inventory_strategy,
        runtime_db_path=inputs.runtime_db_path or db_path,
    )
    summary = generate_tasks_from_sources(preview_inputs)
    return persist_task_generation_summary(summary, db_path=db_path, trade_date=inputs.trade_date)


def persist_task_generation_summary(
    summary: TaskGenerationSummary,
    *,
    db_path: Path = DEFAULT_RUNTIME_DB,
    trade_date: date | None = None,
) -> RuntimeTaskGenerationSummary:
    repository = SQLiteRuntimeRepository(db_path)
    runtime_task_service = RuntimeTaskService(repository)
    runtime_task_service.init_schema()
    inserted_task_rows = runtime_task_service.create_tasks_returning_inserted(
        summary.tasks,
        trade_date=trade_date,
    )
    review_summary = ReviewTaskService(repository, runtime_task_service=runtime_task_service).create_from_tasks(
        inserted_task_rows,
        trade_date=trade_date,
    )
    return RuntimeTaskGenerationSummary(
        validation=summary.validation,
        tasks=summary.tasks,
        inserted_tasks_count=len(inserted_task_rows),
        inserted_review_tasks_count=review_summary.inserted_review_tasks_count,
        inserted_notification_logs_count=review_summary.inserted_notification_logs_count,
        db_path=db_path,
        notification_errors=review_summary.notification_errors,
    )


def list_runtime_tasks(
    db_path: Path = DEFAULT_RUNTIME_DB,
    *,
    trade_date: date | None = None,
    status: TaskStatus | None = None,
    action_type: TaskActionType | None = None,
    scope_type: str | None = None,
    scope_key: str | None = None,
) -> list[Task]:
    repository = SQLiteRuntimeRepository(db_path)
    service = RuntimeTaskService(repository)
    service.init_schema()
    service.expire_overdue_pending_tasks()
    return service.list_tasks(
        trade_date=trade_date,
        status=status,
        action_type=action_type,
        scope_type=scope_type,
        scope_key=scope_key,
    )


def list_runtime_task_history(db_path: Path, task_id: str) -> list[TaskStatusHistory]:
    repository = SQLiteRuntimeRepository(db_path)
    RuntimeTaskService(repository).init_schema()
    return RuntimeTaskService(repository).list_status_history(task_id)


def get_runtime_task(db_path: Path, task_id: str) -> Task | None:
    repository = SQLiteRuntimeRepository(db_path)
    RuntimeTaskService(repository).init_schema()
    return RuntimeTaskService(repository).get_task(task_id)


def list_runtime_review_tasks(
    db_path: Path = DEFAULT_RUNTIME_DB,
    *,
    trade_date: date | None = None,
    status: ReviewTaskStatus | None = None,
) -> list[ReviewTask]:
    repository = SQLiteRuntimeRepository(db_path)
    RuntimeTaskService(repository).init_schema()
    return ReviewTaskService(repository).list_review_tasks(trade_date=trade_date, status=status)


def get_runtime_review_task(db_path: Path, review_task_id: str) -> ReviewTask | None:
    repository = SQLiteRuntimeRepository(db_path)
    RuntimeTaskService(repository).init_schema()
    return ReviewTaskService(repository).get_review_task(review_task_id)


def resolve_runtime_review_task(inputs: RuntimeReviewResolutionInputs) -> ReviewTask:
    repository = SQLiteRuntimeRepository(inputs.db_path)
    runtime_task_service = RuntimeTaskService(repository)
    runtime_task_service.init_schema()
    return ReviewTaskService(repository, runtime_task_service=runtime_task_service).resolve_review_task(
        review_task_id=inputs.review_task_id,
        status=inputs.status,
        actor=inputs.actor,
        actor_source=inputs.actor_source,
        note=inputs.note,
        resolution_payload=inputs.resolution_payload,
        source_task_status=inputs.source_task_status,
    )


def get_mobile_review_detail(db_path: Path, review_task_id: str, raw_token: str) -> MobileReviewDetail:
    repository = SQLiteRuntimeRepository(db_path)
    runtime_task_service = RuntimeTaskService(repository)
    runtime_task_service.init_schema()
    token_service = ReviewTokenService(repository)
    validation = token_service.validate_token(review_task_id, raw_token, action=None)
    if not validation.is_valid or validation.review_token is None or validation.review_task is None:
        raise ValidationError("链接已失效或无权访问该复核任务")
    review_token = token_service.record_detail_access(validation.review_token.token_id)
    source_task = (
        runtime_task_service.get_task(validation.review_task.source_task_id)
        if validation.review_task.source_task_id
        else None
    )
    allowed_actions = [
        action
        for action in review_token.allowed_actions
        if action in {"approved", "rejected", "adjusted", "cancelled"}
    ]
    return MobileReviewDetail(
        review_task=validation.review_task,
        review_token=review_token,
        source_task=source_task,
        allowed_actions=allowed_actions,
    )


def resolve_mobile_review(
    db_path: Path,
    review_task_id: str,
    raw_token: str,
    action: str,
    note: str = "",
    resolution_payload: dict[str, object] | None = None,
) -> MobileReviewResolutionSummary:
    repository = SQLiteRuntimeRepository(db_path)
    runtime_task_service = RuntimeTaskService(repository)
    runtime_task_service.init_schema()
    token_service = ReviewTokenService(repository)
    try:
        review_status = ReviewTaskStatus(action)
    except ValueError as exc:
        raise MobileReviewTransactionError(
            MobileReviewErrorCode.ACTION_NOT_ALLOWED_FOR_REVIEW_TYPE,
            "链接已失效或无权访问该复核任务",
        ) from exc

    token_hash = token_service._hash_raw_token(raw_token)
    atomic_result = repository.resolve_mobile_review_atomic(
        review_task_id=review_task_id,
        token_hash=token_hash,
        status=review_status,
        actor_source="mobile_review_token",
        note=note,
        resolution_payload=resolution_payload,
    )
    return MobileReviewResolutionSummary(
        review_task=atomic_result.review_task,
        review_token=atomic_result.review_token,
        source_task=atomic_result.source_task,
        source_task_status=atomic_result.source_task_status,
    )


def source_task_status_for_review_resolution(source_task: Task | None, status: ReviewTaskStatus) -> TaskStatus | None:
    if source_task is None:
        return None
    if source_task.task_status == TaskStatus.MANUAL_REVIEW:
        return _source_task_status_for_manual_review_source(status)
    if source_task.task_status == TaskStatus.PENDING and source_task.action_type in MANUAL_INTERVENTION_ACTIONS:
        if status == ReviewTaskStatus.CANCELLED:
            return TaskStatus.CANCELLED
        if status == ReviewTaskStatus.ADJUSTED:
            return TaskStatus.PENDING
        return TaskStatus.SKIPPED
    return None


def _source_task_status_for_manual_review_source(status: ReviewTaskStatus) -> TaskStatus:
    if status in {ReviewTaskStatus.APPROVED, ReviewTaskStatus.ADJUSTED}:
        return TaskStatus.PENDING
    if status == ReviewTaskStatus.CANCELLED:
        return TaskStatus.CANCELLED
    return TaskStatus.SKIPPED


def expire_runtime_review_tasks(inputs: ExpireReviewTasksInputs) -> ExpireReviewTasksSummary:
    repository = SQLiteRuntimeRepository(inputs.db_path)
    runtime_task_service = RuntimeTaskService(repository)
    runtime_task_service.init_schema()
    return ReviewTaskService(repository, runtime_task_service=runtime_task_service).expire_pending_review_tasks(
        now=inputs.now,
        apply=inputs.apply,
        enable_notification=inputs.enable_notification,
    )


def list_runtime_notification_logs(
    db_path: Path = DEFAULT_RUNTIME_DB,
    *,
    related_task_id: str | None = None,
    related_review_task_id: str | None = None,
    send_status: str | None = None,
    channel: str | None = None,
) -> list[NotificationLog]:
    repository = SQLiteRuntimeRepository(db_path)
    RuntimeTaskService(repository).init_schema()
    logs = NotificationLogService(repository).list_logs(
        related_review_task_id=related_review_task_id,
        send_status=send_status,
        channel=channel,
    )
    if related_task_id:
        logs = [log for log in logs if log.related_task_id == related_task_id]
    return logs


def get_runtime_notification_log(db_path: Path, notification_id: str) -> NotificationLog | None:
    repository = SQLiteRuntimeRepository(db_path)
    RuntimeTaskService(repository).init_schema()
    return NotificationLogService(repository).get_log(notification_id)


def list_runtime_execution_logs(
    db_path: Path = DEFAULT_RUNTIME_DB,
    *,
    task_id: str | None = None,
    limit: int | None = None,
) -> list[ExecutionLog]:
    repository = SQLiteRuntimeRepository(db_path)
    RuntimeTaskService(repository).init_schema()
    return repository.list_execution_logs(task_id=task_id, limit=limit)


def validate_sources(inputs: WorkflowInputs) -> ValidationSummary:
    products = load_products(inputs.products_path)
    price_rules = load_price_rules(inputs.price_rules_path)
    listing_rules = load_listing_rules(inputs.listing_rules_path)
    harvest_forecasts = load_harvest_forecasts(inputs.harvest_forecasts_path) if inputs.harvest_forecasts_path else None
    price_forecasts = load_price_forecasts(inputs.price_forecasts_path) if inputs.price_forecasts_path else None
    capacity_plan = load_capacity_plan(inputs.capacity_plan_path) if inputs.capacity_plan_path else None
    cold_storage_status = (
        load_cold_storage_status(inputs.cold_storage_status_path) if inputs.cold_storage_status_path else None
    )
    return ValidationSummary(
        products=products,
        price_rules_count=len(price_rules),
        listing_rules_count=len(listing_rules),
        harvest_forecasts=harvest_forecasts,
        price_forecasts=price_forecasts,
        capacity_plan=capacity_plan,
        cold_storage_status=cold_storage_status,
    )


def generate_tasks_from_sources(inputs: WorkflowInputs) -> TaskGenerationSummary:
    products = load_products(inputs.products_path)
    price_rules = load_price_rules(inputs.price_rules_path)
    listing_rules = load_listing_rules(inputs.listing_rules_path)
    harvest_forecasts = load_harvest_forecasts(inputs.harvest_forecasts_path) if inputs.harvest_forecasts_path else None
    price_forecasts = load_price_forecasts(inputs.price_forecasts_path) if inputs.price_forecasts_path else None
    capacity_plan = load_capacity_plan(inputs.capacity_plan_path) if inputs.capacity_plan_path else None
    cold_storage_status = (
        load_cold_storage_status(inputs.cold_storage_status_path) if inputs.cold_storage_status_path else None
    )

    ai_provider = MockAISuggestionProvider() if inputs.use_mock_ai else NullAISuggestionProvider()
    pricing_service = PricingService(ai_provider=ai_provider)
    listing_service = ListingService()
    generator = TaskGenerationService(pricing_service=pricing_service, listing_service=listing_service)
    resolved_platform_name = _resolve_runtime_platform_name(inputs.runtime_db_path, inputs.platform_name)
    old_prices = _load_current_platform_prices(inputs.runtime_db_path, resolved_platform_name)
    tasks = generator.generate(
        products=products,
        price_rules=price_rules,
        listing_rules=listing_rules,
        platform_name=resolved_platform_name,
        harvest_forecasts=harvest_forecasts,
        price_forecasts=price_forecasts,
        capacity_plan=capacity_plan,
        cold_storage_status=cold_storage_status,
        trade_date=inputs.trade_date,
        now=inputs.now,
        inventory_strategy=inputs.inventory_strategy,
        old_prices=old_prices,
    )

    output_written = inputs.output_path is not None
    if output_written:
        export_tasks(inputs.output_path, tasks)

    return TaskGenerationSummary(
        validation=ValidationSummary(
            products=products,
            price_rules_count=len(price_rules),
            listing_rules_count=len(listing_rules),
            harvest_forecasts=harvest_forecasts,
            price_forecasts=price_forecasts,
            capacity_plan=capacity_plan,
            cold_storage_status=cold_storage_status,
        ),
        tasks=tasks,
        output_path=inputs.output_path,
        output_written=output_written,
    )


def generate_tasks_from_selected_rule(
    inputs: WorkflowInputs,
    *,
    rule_type: str,
    rule_id: str,
    task_group_id: str | None = None,
    required_by: datetime | None = None,
) -> TaskGenerationSummary:
    normalized_type = str(rule_type or "").strip().lower()
    selected_id = str(rule_id or "").strip()
    if normalized_type not in {"price", "listing"}:
        raise ValidationError("单规则生成的规则类型只能是 price 或 listing")
    if not selected_id:
        raise ValidationError("请选择要生成任务的规则")

    products = load_products(inputs.products_path)
    price_rules = [load_price_rule(inputs.price_rules_path, selected_id)] if normalized_type == "price" else []
    listing_rules = [load_listing_rule(inputs.listing_rules_path, selected_id)] if normalized_type == "listing" else []
    selected_rule = price_rules[0] if price_rules else listing_rules[0]
    platform_names = _resolve_selected_rule_platforms(
        selected_rule.platform_filter,
        inputs,
        require_online_price=normalized_type == "price",
    )
    resolved_group_id = str(task_group_id or "").strip() or f"RULE-GROUP-{uuid4().hex[:12]}"
    resolved_required_by = required_by or datetime.now(timezone.utc) + timedelta(minutes=30)
    ai_provider = MockAISuggestionProvider() if inputs.use_mock_ai else NullAISuggestionProvider()
    generator = TaskGenerationService(
        pricing_service=PricingService(ai_provider=ai_provider),
        listing_service=ListingService(),
    )
    tasks: list[Task] = []
    for platform_name in platform_names:
        old_prices = _load_current_platform_prices(inputs.runtime_db_path, platform_name)
        generated = generator.generate(
            products=products,
            price_rules=price_rules,
            listing_rules=listing_rules,
            platform_name=platform_name,
            old_prices=old_prices,
        )
        if normalized_type == "price":
            selected_tasks = [
                task
                for task in generated
                if task.action_type == TaskActionType.UPDATE_PRICE
                and selected_id in task.decision_trace.get("matched_rule_ids", [])
            ]
        else:
            marker = f"matched:{selected_id}:"
            selected_tasks = [
                task
                for task in generated
                if any(marker in str(step) for step in task.decision_trace.get("listing_trace", []))
            ]
        for task in selected_tasks:
            task.decision_trace = dict(task.decision_trace) | {
                "generation_mode": "single_rule",
                "selected_rule_type": normalized_type,
                "selected_rule_id": selected_id,
                "selected_rule_platform_filter": selected_rule.platform_filter,
                "resolved_platform_name": platform_name,
                "task_group_id": resolved_group_id,
            }
            task.required_by = resolved_required_by
        tasks.extend(selected_tasks)

    output_written = inputs.output_path is not None
    if output_written:
        export_tasks(inputs.output_path, tasks)
    return TaskGenerationSummary(
        validation=ValidationSummary(
            products=products,
            price_rules_count=len(price_rules),
            listing_rules_count=len(listing_rules),
        ),
        tasks=tasks,
        output_path=inputs.output_path,
        output_written=output_written,
    )


def preview_tasks_from_selected_rule(
    inputs: WorkflowInputs,
    *,
    rule_type: str,
    rule_id: str,
    task_group_id: str | None = None,
    required_by: datetime | None = None,
) -> TaskGenerationSummary:
    preview_inputs = WorkflowInputs(
        products_path=inputs.products_path,
        price_rules_path=inputs.price_rules_path,
        listing_rules_path=inputs.listing_rules_path,
        output_path=None,
        platform_name=inputs.platform_name,
        use_mock_ai=inputs.use_mock_ai,
        inventory_strategy=inputs.inventory_strategy,
        runtime_db_path=inputs.runtime_db_path,
        platform_names=inputs.platform_names,
    )
    return generate_tasks_from_selected_rule(
        preview_inputs,
        rule_type=rule_type,
        rule_id=rule_id,
        task_group_id=task_group_id,
        required_by=required_by,
    )


def _resolve_selected_rule_platforms(
    platform_filter: str,
    inputs: WorkflowInputs,
    *,
    require_online_price: bool,
) -> list[str]:
    rule_platform = str(platform_filter or "").strip()
    if rule_platform and rule_platform != "*":
        if inputs.runtime_db_path is not None:
            repository = SQLiteRuntimeRepository(inputs.runtime_db_path)
            repository.init_schema()
            matching_online_platforms = list(dict.fromkeys(
                status.platform_name
                for status in repository.list_listing_statuses()
                if is_price_task_listing(status)
                and platform_names_match(rule_platform, status.platform_name)
            ))
            if matching_online_platforms:
                canonical_name = canonical_platform_name(rule_platform)
                if canonical_name in matching_online_platforms:
                    return [canonical_name]
                return [matching_online_platforms[0]]
        return [canonical_platform_name(rule_platform)]
    candidates = list(dict.fromkeys(
        platform
        for platform in (*inputs.platform_names, inputs.platform_name)
        if platform and platform != "default_platform"
    ))
    if require_online_price and inputs.runtime_db_path is not None:
        repository = SQLiteRuntimeRepository(inputs.runtime_db_path)
        repository.init_schema()
        listing_statuses = repository.list_listing_statuses()
        online_platforms = list(dict.fromkeys(
            status.platform_name
            for status in listing_statuses
            if is_price_task_listing(status)
        ))
        if not online_platforms and listing_statuses:
            online_platforms = list(dict.fromkeys(status.platform_name for status in listing_statuses))
        resolved_candidates: list[str] = []
        resolved_keys: set[str] = set()
        for candidate in candidates:
            matching_platform = next(
                (platform for platform in online_platforms if platform_names_match(candidate, platform)),
                None,
            )
            if matching_platform is None:
                continue
            identity = platform_identity_key(matching_platform)
            if identity not in resolved_keys:
                resolved_keys.add(identity)
                resolved_candidates.append(matching_platform)
        candidates = resolved_candidates
    if not candidates:
        if require_online_price:
            raise ValidationError("全平台价格规则未找到带在线价格快照的平台，请先运行 ShadowBot READ_ONLY")
        raise ValidationError("全平台规则未找到有效平台配置")
    return candidates


def _load_current_platform_prices(
    runtime_db_path: Path | None,
    platform_name: str,
) -> dict[tuple[str, str, str], Decimal] | None:
    if runtime_db_path is None:
        return None
    repository = SQLiteRuntimeRepository(runtime_db_path)
    repository.init_schema()
    return {
        listing_identity_key(platform_name, status.variety, status.grade): status.current_price
        for status in repository.list_listing_statuses()
        if is_price_task_listing(status) and platform_names_match(platform_name, status.platform_name)
    }


def _resolve_runtime_platform_name(runtime_db_path: Path | None, platform_name: str) -> str:
    canonical_name = canonical_platform_name(platform_name)
    if runtime_db_path is None:
        return canonical_name
    repository = SQLiteRuntimeRepository(runtime_db_path)
    repository.init_schema()
    matching_platforms = list(dict.fromkeys(
        status.platform_name
        for status in repository.list_listing_statuses()
        if platform_names_match(platform_name, status.platform_name)
    ))
    if canonical_name in matching_platforms:
        return canonical_name
    return matching_platforms[0] if matching_platforms else canonical_name


def preview_tasks_from_sources(inputs: WorkflowInputs) -> TaskGenerationSummary:
    preview_inputs = WorkflowInputs(
        products_path=inputs.products_path,
        price_rules_path=inputs.price_rules_path,
        listing_rules_path=inputs.listing_rules_path,
        output_path=None,
        platform_name=inputs.platform_name,
        use_mock_ai=inputs.use_mock_ai,
        harvest_forecasts_path=inputs.harvest_forecasts_path,
        price_forecasts_path=inputs.price_forecasts_path,
        capacity_plan_path=inputs.capacity_plan_path,
        cold_storage_status_path=inputs.cold_storage_status_path,
        trade_date=inputs.trade_date,
        now=inputs.now,
        inventory_strategy=inputs.inventory_strategy,
        runtime_db_path=inputs.runtime_db_path,
    )
    return generate_tasks_from_sources(preview_inputs)


def simulate_execution_from_tasks(inputs: ExecutionSimulationInputs) -> ExecutionSimulationSummary:
    tasks = load_tasks(inputs.tasks_path)
    service = ExecutionSimulationService()
    updated_tasks, logs = service.simulate(tasks, executor_name=inputs.executor_name)
    export_execution_logs(inputs.logs_output_path, logs)
    if inputs.updated_tasks_output_path is not None:
        export_tasks(inputs.updated_tasks_output_path, updated_tasks)
    return ExecutionSimulationSummary(
        source_tasks_path=inputs.tasks_path,
        logs_output_path=inputs.logs_output_path,
        updated_tasks_output_path=inputs.updated_tasks_output_path,
        tasks=updated_tasks,
        logs=logs,
    )


def list_manual_intervention_tasks(tasks_path: Path) -> list[Task]:
    tasks = load_tasks(tasks_path)
    return ManualInterventionService().list_open_tasks(tasks)


def resolve_manual_intervention_task(inputs: ManualInterventionInputs) -> ManualInterventionSummary:
    raise ValidationError(
        "旧 Excel 人工介入入口已弃用，不能再执行正式处理。请改用 SQLite review_tasks 或 Web /runtime 入口。"
    )

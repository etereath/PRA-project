from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.enums import ReviewTaskStatus, TaskActionType, TaskStatus
from app.exceptions import ValidationError
from app.models import (
    ColdStorageStatus,
    ExecutionLog,
    HarvestForecast,
    NotificationLog,
    PackingCapacityPlan,
    PriceForecast,
    Product,
    ReviewTask,
    Task,
    TaskStatusHistory,
)
from app.repositories.workbook_repository import (
    export_execution_logs,
    export_tasks,
    load_capacity_plan,
    load_cold_storage_status,
    load_harvest_forecasts,
    load_listing_rules,
    load_price_forecasts,
    load_price_rules,
    load_products,
    load_tasks,
)
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.ai import MockAISuggestionProvider, NullAISuggestionProvider
from app.services.execution import ExecutionSimulationService
from app.services.listing import ListingService
from app.services.manual_intervention import ManualInterventionService
from app.services.pricing import PricingService
from app.services.runtime import (
    DEFAULT_RUNTIME_DB,
    ExpireReviewTasksSummary,
    NotificationLogService,
    ReviewTaskService,
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
    )
    summary = generate_tasks_from_sources(preview_inputs)
    repository = SQLiteRuntimeRepository(db_path)
    runtime_task_service = RuntimeTaskService(repository)
    runtime_task_service.init_schema()
    inserted_tasks = runtime_task_service.create_tasks(summary.tasks, trade_date=inputs.trade_date)
    review_summary = ReviewTaskService(repository, runtime_task_service=runtime_task_service).create_from_tasks(
        summary.tasks,
        trade_date=inputs.trade_date,
    )
    return RuntimeTaskGenerationSummary(
        validation=summary.validation,
        tasks=summary.tasks,
        inserted_tasks_count=inserted_tasks,
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
) -> list[Task]:
    repository = SQLiteRuntimeRepository(db_path)
    RuntimeTaskService(repository).init_schema()
    return RuntimeTaskService(repository).list_tasks(
        trade_date=trade_date,
        status=status,
        action_type=action_type,
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
    related_review_task_id: str | None = None,
    send_status: str | None = None,
) -> list[NotificationLog]:
    repository = SQLiteRuntimeRepository(db_path)
    RuntimeTaskService(repository).init_schema()
    return NotificationLogService(repository).list_logs(
        related_review_task_id=related_review_task_id,
        send_status=send_status,
    )


def get_runtime_notification_log(db_path: Path, notification_id: str) -> NotificationLog | None:
    repository = SQLiteRuntimeRepository(db_path)
    RuntimeTaskService(repository).init_schema()
    return NotificationLogService(repository).get_log(notification_id)


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
    tasks = generator.generate(
        products=products,
        price_rules=price_rules,
        listing_rules=listing_rules,
        platform_name=inputs.platform_name,
        harvest_forecasts=harvest_forecasts,
        price_forecasts=price_forecasts,
        capacity_plan=capacity_plan,
        cold_storage_status=cold_storage_status,
        trade_date=inputs.trade_date,
        now=inputs.now,
        inventory_strategy=inputs.inventory_strategy,
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

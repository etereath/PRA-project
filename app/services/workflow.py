from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.models import ExecutionLog, Product, Task
from app.repositories.workbook_repository import (
    export_execution_logs,
    export_tasks,
    load_listing_rules,
    load_price_rules,
    load_products,
    load_tasks,
)
from app.services.ai import MockAISuggestionProvider, NullAISuggestionProvider
from app.services.execution import ExecutionSimulationService
from app.services.listing import ListingService
from app.services.pricing import PricingService
from app.services.task_generation import TaskGenerationService


@dataclass(slots=True)
class WorkflowInputs:
    products_path: Path
    price_rules_path: Path
    listing_rules_path: Path
    output_path: Path | None = None
    platform_name: str = "default_platform"
    use_mock_ai: bool = False


@dataclass(slots=True)
class ValidationSummary:
    products: list[Product]
    price_rules_count: int
    listing_rules_count: int


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


def validate_sources(inputs: WorkflowInputs) -> ValidationSummary:
    products = load_products(inputs.products_path)
    price_rules = load_price_rules(inputs.price_rules_path)
    listing_rules = load_listing_rules(inputs.listing_rules_path)
    return ValidationSummary(
        products=products,
        price_rules_count=len(price_rules),
        listing_rules_count=len(listing_rules),
    )


def generate_tasks_from_sources(inputs: WorkflowInputs) -> TaskGenerationSummary:
    products = load_products(inputs.products_path)
    price_rules = load_price_rules(inputs.price_rules_path)
    listing_rules = load_listing_rules(inputs.listing_rules_path)

    ai_provider = MockAISuggestionProvider() if inputs.use_mock_ai else NullAISuggestionProvider()
    pricing_service = PricingService(ai_provider=ai_provider)
    listing_service = ListingService()
    generator = TaskGenerationService(pricing_service=pricing_service, listing_service=listing_service)
    tasks = generator.generate(
        products=products,
        price_rules=price_rules,
        listing_rules=listing_rules,
        platform_name=inputs.platform_name,
    )

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


def preview_tasks_from_sources(inputs: WorkflowInputs) -> TaskGenerationSummary:
    preview_inputs = WorkflowInputs(
        products_path=inputs.products_path,
        price_rules_path=inputs.price_rules_path,
        listing_rules_path=inputs.listing_rules_path,
        output_path=None,
        platform_name=inputs.platform_name,
        use_mock_ai=inputs.use_mock_ai,
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

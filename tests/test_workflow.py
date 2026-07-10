from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from app.enums import ReviewTaskStatus, TaskStatus
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import (
    CAPACITY_PLAN_HEADERS,
    HARVEST_FORECAST_HEADERS,
    LISTING_RULE_HEADERS,
    PRICE_RULE_HEADERS,
    PRODUCT_HEADERS,
)
from app.services.runtime import ReviewTaskService, RuntimeTaskService
from app.services.workflow import (
    ExecutionSimulationInputs,
    WorkflowInputs,
    generate_tasks_from_sources,
    generate_runtime_tasks_from_sources,
    preview_tasks_from_sources,
    simulate_execution_from_tasks,
    validate_sources,
)


def _write_workbook(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.products_path = root / "products.xlsx"
        self.price_rules_path = root / "price_rules.xlsx"
        self.listing_rules_path = root / "listing_rules.xlsx"
        self.output_path = root / "tasks.xlsx"

        _write_workbook(
            self.products_path,
            PRODUCT_HEADERS,
            [
                ["SKU-001", "艾莎", "A", "70", "扎", 10, 50, True, 14, 15, "", "spring", "red"],
                ["SKU-002", "艾莎", "B", "60", "扎", 10, 0, True, 13, 14, "", "spring", "white"],
                ["SKU-003", "卡布奇诺", "A", "70", "扎", 10, 12, False, 12, 13, "", "summer", "pink"],
            ],
        )
        _write_workbook(
            self.price_rules_path,
            PRICE_RULE_HEADERS,
            [
                ["RULE-ALL-1", "全局固定加价", "*", "*", "*", "fixed_markup", 5, 14, "round", "", True, 10, ""],
                ["RULE-A", "A级加价", "*", "A", "*", "percentage_markup", 10, "", "step", 0.5, True, 20, ""],
            ],
        )
        _write_workbook(
            self.listing_rules_path,
            LISTING_RULE_HEADERS,
            [
                ["LIST-LOW", "库存不足下架", "*", "*", "*", 0, "stock_below_offline", True, 1, ""],
                ["LIST-RESTOCK", "库存恢复允许上架", "*", "*", "*", 10, "stock_above_online", True, 5, ""],
            ],
        )

        self.inputs = WorkflowInputs(
            products_path=self.products_path,
            price_rules_path=self.price_rules_path,
            listing_rules_path=self.listing_rules_path,
            output_path=self.output_path,
            use_mock_ai=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_validate_sources_counts_expected_records(self) -> None:
        summary = validate_sources(self.inputs)
        self.assertEqual(len(summary.products), 3)
        self.assertEqual(summary.price_rules_count, 2)
        self.assertEqual(summary.listing_rules_count, 2)

    def test_generate_tasks_returns_summary_and_counts(self) -> None:
        summary = generate_tasks_from_sources(self.inputs)
        self.assertEqual(len(summary.tasks), 4)
        self.assertEqual(summary.task_counts["update_price"], 1)
        self.assertEqual(summary.task_counts["set_offline"], 2)
        self.assertTrue(summary.output_written)

    def test_preview_tasks_does_not_write_output(self) -> None:
        preview = preview_tasks_from_sources(self.inputs)
        self.assertEqual(len(preview.tasks), 4)
        self.assertFalse(preview.output_written)
        self.assertIsNone(preview.output_path)

    def test_simulate_execution_writes_logs_and_updates_tasks(self) -> None:
        summary = generate_tasks_from_sources(self.inputs)
        self.assertEqual(len(summary.tasks), 4)

        logs_output = Path(self.temp_dir.name) / "execution_logs.xlsx"
        updated_tasks_output = Path(self.temp_dir.name) / "executed_tasks.xlsx"
        execution = simulate_execution_from_tasks(
            ExecutionSimulationInputs(
                tasks_path=self.output_path,
                logs_output_path=logs_output,
                updated_tasks_output_path=updated_tasks_output,
                executor_name="test_executor",
            )
        )

        self.assertEqual(len(execution.logs), 4)
        self.assertEqual(execution.success_count, 4)
        self.assertTrue(logs_output.exists())
        self.assertTrue(updated_tasks_output.exists())
        self.assertTrue(all(task.task_status.value == "success" for task in execution.tasks))

    def test_runtime_generation_does_not_create_reviews_for_duplicate_tasks(self) -> None:
        trade_date = date(2026, 5, 8)
        harvest_path = Path(self.temp_dir.name) / "harvest_forecasts.xlsx"
        capacity_path = Path(self.temp_dir.name) / "capacity_plans.xlsx"
        runtime_db = Path(self.temp_dir.name) / "runtime.sqlite3"
        _write_workbook(
            harvest_path,
            HARVEST_FORECAST_HEADERS,
            [["HF-1", date(2026, 5, 7), trade_date, "艾莎", "A", 420, 380, 460, 0.8, "manual", datetime(2026, 5, 7, 12, 0), ""]],
        )
        _write_workbook(
            capacity_path,
            CAPACITY_PLAN_HEADERS,
            [[trade_date, 250, 100, 0, 250, "proportional_by_forecast", True, ""]],
        )
        inputs = WorkflowInputs(
            products_path=self.products_path,
            price_rules_path=self.price_rules_path,
            listing_rules_path=self.listing_rules_path,
            harvest_forecasts_path=harvest_path,
            capacity_plan_path=capacity_path,
            trade_date=trade_date,
        )
        with patch.dict("os.environ", {"DEFAULT_NOTIFICATION_CHANNEL": "mock"}, clear=False):
            first = generate_runtime_tasks_from_sources(inputs, db_path=runtime_db)
        repository = SQLiteRuntimeRepository(runtime_db)
        task_service = RuntimeTaskService(repository)
        review_service = ReviewTaskService(repository, runtime_task_service=task_service)
        for review in review_service.list_review_tasks():
            review_service.resolve_review_task(
                review_task_id=review.review_task_id,
                status=ReviewTaskStatus.CANCELLED,
                actor="test",
            )

        with patch.dict("os.environ", {"DEFAULT_NOTIFICATION_CHANNEL": "mock"}, clear=False):
            second = generate_runtime_tasks_from_sources(inputs, db_path=runtime_db)

        self.assertGreaterEqual(first.inserted_tasks_count, 2)
        self.assertEqual(first.inserted_review_tasks_count, 2)
        self.assertEqual(second.inserted_tasks_count, 0)
        self.assertEqual(second.inserted_review_tasks_count, 0)
        reviews = review_service.list_review_tasks()
        self.assertEqual(len(reviews), 2)
        self.assertTrue(all(review.source_task_id for review in reviews))
        self.assertTrue(all(task_service.get_task(str(review.source_task_id)) is not None for review in reviews))
        self.assertTrue(all(task.task_status == TaskStatus.PENDING for task in task_service.list_tasks()))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from app.enums import ReviewTaskStatus, TaskStatus
from app.models import ListingStatus
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
    generate_tasks_from_selected_rule,
    preview_tasks_from_sources,
    preview_tasks_from_selected_rule,
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
                ["RULE-ALL-1", "全局固定加价", "*", "*", "测试平台", "fixed_markup", 5, 14, "round", "", True, 10, ""],
                ["RULE-A", "A级加价", "*", "A", "测试平台", "percentage_markup", 10, "", "step", 0.5, True, 20, ""],
            ],
        )
        _write_workbook(
            self.listing_rules_path,
            LISTING_RULE_HEADERS,
            [
                ["LIST-LOW", "库存不足下架", "*", "*", "测试平台", 0, "stock_below_offline", True, 1, ""],
                ["LIST-RESTOCK", "库存恢复允许上架", "*", "*", "测试平台", 10, "stock_above_online", True, 5, ""],
            ],
        )

        self.inputs = WorkflowInputs(
            products_path=self.products_path,
            price_rules_path=self.price_rules_path,
            listing_rules_path=self.listing_rules_path,
            output_path=self.output_path,
            platform_name="测试平台",
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

    def test_selected_price_rule_generates_only_tasks_matched_by_that_rule(self) -> None:
        preview = preview_tasks_from_selected_rule(
            self.inputs,
            rule_type="price",
            rule_id="RULE-A",
        )
        self.assertEqual([task.internal_sku for task in preview.tasks], ["SKU-001"])
        self.assertTrue(all(task.action_type.value == "update_price" for task in preview.tasks))
        self.assertTrue(all(task.decision_trace["selected_rule_id"] == "RULE-A" for task in preview.tasks))
        self.assertTrue(all(task.decision_trace["task_group_id"] for task in preview.tasks))
        self.assertEqual(len({task.required_by for task in preview.tasks}), 1)
        self.assertEqual({task.platform_name for task in preview.tasks}, {"测试平台"})
        deadline = preview.tasks[0].required_by
        assert deadline is not None
        remaining = deadline - datetime.now(deadline.tzinfo)
        self.assertGreater(remaining, timedelta(minutes=29))
        self.assertLessEqual(remaining, timedelta(minutes=30))
        self.assertFalse(preview.output_written)

    def test_selected_rule_expands_wildcard_to_configured_platforms(self) -> None:
        _write_workbook(
            self.price_rules_path,
            PRICE_RULE_HEADERS,
            [["RULE-WILDCARD", "全平台规则", "*", "*", "*", "fixed_markup", 2, "", "none", "", True, 1, ""]],
        )
        self.inputs.platform_names = ("测试平台", "备用平台")
        preview = preview_tasks_from_selected_rule(
            self.inputs,
            rule_type="price",
            rule_id="RULE-WILDCARD",
        )
        self.assertEqual({task.platform_name for task in preview.tasks}, {"测试平台", "备用平台"})

    def test_selected_listing_rule_generates_only_tasks_caused_by_that_rule(self) -> None:
        summary = generate_tasks_from_selected_rule(
            self.inputs,
            rule_type="listing",
            rule_id="LIST-LOW",
        )
        self.assertEqual([task.internal_sku for task in summary.tasks], ["SKU-002"])
        self.assertTrue(all(task.action_type.value == "set_offline" for task in summary.tasks))
        self.assertTrue(all(task.decision_trace["selected_rule_id"] == "LIST-LOW" for task in summary.tasks))
        self.assertEqual(len({task.decision_trace["task_group_id"] for task in summary.tasks}), 1)
        self.assertTrue(summary.output_written)

    def test_selected_rule_loader_ignores_invalid_unselected_rule(self) -> None:
        _write_workbook(
            self.price_rules_path,
            PRICE_RULE_HEADERS,
            [
                ["RULE-VALID", "有效规则", "*", "*", "测试平台", "fixed_markup", 5, "", "none", "", True, 1, ""],
                ["RULE-BROKEN", "无效规则", "*", "*", "测试平台", "fixed_markup", 0, "", "none", "", True, 2, ""],
            ],
        )
        preview = preview_tasks_from_selected_rule(
            self.inputs,
            rule_type="price",
            rule_id="RULE-VALID",
        )
        self.assertEqual([task.internal_sku for task in preview.tasks], ["SKU-001"])

    def test_selected_rule_uses_concrete_rule_platform_and_shared_group_metadata(self) -> None:
        _write_workbook(
            self.price_rules_path,
            PRICE_RULE_HEADERS,
            [["RULE-PLATFORM", "指定平台", "艾莎", "*", "花盛锦", "fixed_markup", 2, "", "none", "", True, 1, ""]],
        )
        deadline = datetime(2026, 7, 22, 18, 0)
        preview = preview_tasks_from_selected_rule(
            self.inputs,
            rule_type="price",
            rule_id="RULE-PLATFORM",
            task_group_id="RULE-GROUP-001",
            required_by=deadline,
        )
        self.assertEqual({task.platform_name for task in preview.tasks}, {"花盛锦"})
        self.assertEqual({task.required_by for task in preview.tasks}, {deadline})
        self.assertEqual(
            {task.decision_trace["task_group_id"] for task in preview.tasks},
            {"RULE-GROUP-001"},
        )

    def test_rule_platform_alias_resolves_to_online_listing_platform(self) -> None:
        runtime_db = Path(self.temp_dir.name) / "runtime-platform-alias.sqlite3"
        repository = SQLiteRuntimeRepository(runtime_db)
        repository.init_schema()
        repository.upsert_listing_status(
            ListingStatus(
                listing_status_id="LISTING-ANT-AISHA-A",
                platform_name="蚂蚁花团供应商",
                internal_sku="",
                variety="艾莎",
                grade="A",
                current_price=Decimal("18.50"),
                online_status="online",
                source="shadowbot_read",
            )
        )
        _write_workbook(
            self.price_rules_path,
            PRICE_RULE_HEADERS,
            [["RULE-ANT", "蚂蚁平台加价", "艾莎", "A", "蚂蚁", "fixed_markup", 2, "", "none", "", True, 1, ""]],
        )
        self.inputs.runtime_db_path = runtime_db
        self.inputs.platform_names = ("蚂蚁", "蚂蚁花团供应商")

        preview = preview_tasks_from_selected_rule(
            self.inputs,
            rule_type="price",
            rule_id="RULE-ANT",
        )

        self.assertEqual(len(preview.tasks), 1)
        self.assertEqual(preview.tasks[0].platform_name, "蚂蚁花团供应商")
        self.assertEqual(preview.tasks[0].expected_old_price, Decimal("18.50"))
        self.assertEqual(preview.tasks[0].target_price, Decimal("20.50"))

    def test_batch_platform_alias_uses_canonical_listing_price(self) -> None:
        runtime_db = Path(self.temp_dir.name) / "runtime-batch-platform-alias.sqlite3"
        repository = SQLiteRuntimeRepository(runtime_db)
        repository.init_schema()
        repository.upsert_listing_status(
            ListingStatus(
                listing_status_id="LISTING-ANT-BATCH-A",
                platform_name="蚂蚁花团供应商",
                internal_sku="",
                variety="艾莎",
                grade="A",
                current_price=Decimal("18.50"),
                online_status="online",
            )
        )
        _write_workbook(
            self.price_rules_path,
            PRICE_RULE_HEADERS,
            [["RULE-ANT-BATCH", "蚂蚁批量加价", "艾莎", "A", "蚂蚁", "fixed_markup", 2, "", "none", "", True, 1, ""]],
        )
        self.inputs.platform_name = "蚂蚁"
        self.inputs.runtime_db_path = runtime_db
        self.inputs.output_path = None

        preview = preview_tasks_from_sources(self.inputs)
        price_tasks = [task for task in preview.tasks if task.action_type.value == "update_price"]

        self.assertEqual(len(price_tasks), 1)
        self.assertEqual(price_tasks[0].platform_name, "蚂蚁花团供应商")
        self.assertEqual(price_tasks[0].expected_old_price, Decimal("18.50"))
        self.assertEqual(price_tasks[0].target_price, Decimal("20.50"))

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

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from app.repositories.workbook_repository import LISTING_RULE_HEADERS, PRICE_RULE_HEADERS, PRODUCT_HEADERS
from app.services.workflow import (
    ExecutionSimulationInputs,
    WorkflowInputs,
    generate_tasks_from_sources,
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
                ["RULE-ALL-1", "全局固定加价", "all", "*", "fixed_markup", 5, 14, "round", "", True, 10, ""],
                ["RULE-A", "A级加价", "grade", "A", "percentage_markup", 10, "", "step", 0.5, True, 20, ""],
            ],
        )
        _write_workbook(
            self.listing_rules_path,
            LISTING_RULE_HEADERS,
            [
                ["LIST-LOW", "库存小于等于0下架", "stock_lte", 0, "set_offline", True, 1, ""],
                ["LIST-RESTOCK", "库存大于等于10上架", "stock_gte", 10, "set_online", True, 5, ""],
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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from app.repositories.workbook_repository import LISTING_RULE_HEADERS, PRICE_RULE_HEADERS, PRODUCT_HEADERS
from app.services.workflow import (
    ExecutionSimulationInputs,
    WorkflowInputs,
    generate_tasks_from_sources,
    simulate_execution_from_tasks,
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


class EndToEndRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.golden_dir = Path(__file__).resolve().parent / "golden"
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.products_path = root / "products.xlsx"
        self.price_rules_path = root / "price_rules.xlsx"
        self.listing_rules_path = root / "listing_rules.xlsx"

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

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_generated_tasks_match_golden_snapshot(self) -> None:
        output_path = Path(self.temp_dir.name) / "tasks.xlsx"
        summary = generate_tasks_from_sources(
            WorkflowInputs(
                products_path=self.products_path,
                price_rules_path=self.price_rules_path,
                listing_rules_path=self.listing_rules_path,
                output_path=output_path,
                use_mock_ai=True,
            )
        )

        normalized = [
            {
                "internal_sku": task.internal_sku,
                "action_type": task.action_type.value,
                "priority": task.priority,
                "target_price": str(task.target_price) if task.target_price is not None else None,
                "target_status": task.target_status,
                "pricing_source": task.pricing_source.value if task.pricing_source else None,
            }
            for task in summary.tasks
        ]
        golden = json.loads((self.golden_dir / "generated_tasks_snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(normalized, golden)

    def test_execution_logs_match_golden_snapshot(self) -> None:
        tasks_output = Path(self.temp_dir.name) / "tasks.xlsx"
        logs_output = Path(self.temp_dir.name) / "logs.xlsx"
        updated_tasks_output = Path(self.temp_dir.name) / "executed_tasks.xlsx"
        generate_tasks_from_sources(
            WorkflowInputs(
                products_path=self.products_path,
                price_rules_path=self.price_rules_path,
                listing_rules_path=self.listing_rules_path,
                output_path=tasks_output,
                use_mock_ai=True,
            )
        )

        execution = simulate_execution_from_tasks(
            ExecutionSimulationInputs(
                tasks_path=tasks_output,
                logs_output_path=logs_output,
                updated_tasks_output_path=updated_tasks_output,
                executor_name="golden_executor",
            )
        )

        normalized = [
            {
                "executor_name": log.executor_name,
                "success_flag": log.success_flag,
                "raw_output": log.raw_output,
            }
            for log in execution.logs
        ]
        golden = json.loads((self.golden_dir / "execution_logs_snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(normalized, golden)


if __name__ == "__main__":
    unittest.main()

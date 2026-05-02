from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.workflow import (
    ExecutionSimulationInputs,
    WorkflowInputs,
    generate_tasks_from_sources,
    simulate_execution_from_tasks,
)


class EndToEndRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.golden_dir = Path(__file__).resolve().parent / "golden"

    def test_generated_tasks_match_golden_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "tasks.xlsx"
            summary = generate_tasks_from_sources(
                WorkflowInputs(
                    products_path=self.root / "data" / "samples" / "products.xlsx",
                    price_rules_path=self.root / "data" / "samples" / "price_rules.xlsx",
                    listing_rules_path=self.root / "data" / "samples" / "listing_rules.xlsx",
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
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_output = Path(tmpdir) / "tasks.xlsx"
            logs_output = Path(tmpdir) / "logs.xlsx"
            updated_tasks_output = Path(tmpdir) / "executed_tasks.xlsx"
            generate_tasks_from_sources(
                WorkflowInputs(
                    products_path=self.root / "data" / "samples" / "products.xlsx",
                    price_rules_path=self.root / "data" / "samples" / "price_rules.xlsx",
                    listing_rules_path=self.root / "data" / "samples" / "listing_rules.xlsx",
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

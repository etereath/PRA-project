from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.workflow import (
    ExecutionSimulationInputs,
    WorkflowInputs,
    generate_tasks_from_sources,
    preview_tasks_from_sources,
    simulate_execution_from_tasks,
    validate_sources,
)


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.inputs = WorkflowInputs(
            products_path=root / "data" / "samples" / "products.xlsx",
            price_rules_path=root / "data" / "samples" / "price_rules.xlsx",
            listing_rules_path=root / "data" / "samples" / "listing_rules.xlsx",
            output_path=root / "data" / "samples" / "workflow_test_tasks.xlsx",
            use_mock_ai=True,
        )

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

        with tempfile.TemporaryDirectory() as tmpdir:
            logs_output = Path(tmpdir) / "execution_logs.xlsx"
            updated_tasks_output = Path(tmpdir) / "executed_tasks.xlsx"
            execution = simulate_execution_from_tasks(
                ExecutionSimulationInputs(
                    tasks_path=self.inputs.output_path,
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

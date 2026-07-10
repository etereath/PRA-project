from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.repositories.workbook_repository import (
    CAPACITY_PLAN_HEADERS,
    COLD_STORAGE_STATUS_HEADERS,
    HARVEST_FORECAST_HEADERS,
    LISTING_RULE_HEADERS,
    PRODUCT_HEADERS,
)
from app.services.business_rule_evaluation import (
    RUN_MODE_APPLY,
    RUN_MODE_DRY_RUN,
    BusinessRuleRunner,
    EvaluationContext,
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


class BusinessRuleEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "runtime.sqlite3"
        self.harvest_path = root / "harvest_forecasts.xlsx"
        self.capacity_path = root / "capacity_plans.xlsx"
        self.cold_storage_path = root / "cold_storage_status.xlsx"
        self.products_path = root / "products.xlsx"
        self.listing_rules_path = root / "listing_rules.xlsx"
        self.trade_date = date(2026, 5, 8)
        _write_workbook(
            self.harvest_path,
            HARVEST_FORECAST_HEADERS,
            [
                [
                    "HF-1",
                    date(2026, 5, 7),
                    self.trade_date,
                    "艾莎",
                    "A",
                    420,
                    380,
                    460,
                    0.8,
                    "manual",
                    datetime(2026, 5, 7, 12, 0),
                    "",
                ]
            ],
        )
        _write_workbook(
            self.capacity_path,
            CAPACITY_PLAN_HEADERS,
            [[self.trade_date, 250, 100, 0, 250, "proportional_by_forecast", True, ""]],
        )
        _write_workbook(
            self.cold_storage_path,
            COLD_STORAGE_STATUS_HEADERS,
            [[self.trade_date, 500, 120, 0, 0, 50, 120, 380, True, ""]],
        )
        _write_workbook(
            self.products_path,
            PRODUCT_HEADERS,
            [
                ["SKU-LOW", "艾莎", "A", "65", "扎", 10, 0, True, 14, 15, "", "", ""],
                ["SKU-OK", "艾莎", "B", "60", "扎", 10, 20, True, 14, 15, "", "", ""],
            ],
        )
        _write_workbook(
            self.listing_rules_path,
            LISTING_RULE_HEADERS,
            [["LIST-LOW", "库存不足下架", "*", "*", "*", 0, "stock_below_offline", True, 1, ""]],
        )
        self.repository = SQLiteRuntimeRepository(self.db_path)
        self.repository.init_schema()
        self.runner = BusinessRuleRunner(self.repository)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _context(self, run_mode: str) -> EvaluationContext:
        return EvaluationContext(
            trade_date=self.trade_date,
            runtime_db_path=self.db_path,
            run_mode=run_mode,
            now=datetime(2026, 5, 7, 10, 0),
            harvest_forecasts_path=self.harvest_path,
            capacity_plan_path=self.capacity_path,
            cold_storage_status_path=self.cold_storage_path,
            products_path=self.products_path,
            listing_rules_path=self.listing_rules_path,
            created_by="test",
        )

    def test_dry_run_records_preview_without_business_writes(self) -> None:
        summary = self.runner.run("capacity_warning", self._context(RUN_MODE_DRY_RUN))

        self.assertEqual(summary.script_run.run_mode, RUN_MODE_DRY_RUN)
        self.assertEqual(summary.proposals_count, 2)
        self.assertEqual(summary.inserted_tasks_count, 0)
        self.assertEqual(self.repository.list_tasks(), [])
        self.assertEqual(self.repository.list_review_tasks(), [])
        self.assertEqual(self.repository.list_notification_logs(), [])
        items = self.repository.list_script_run_items(summary.script_run.script_run_id)
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item.item_status == "previewed" for item in items))

    def test_apply_creates_reviews_and_notifications_through_services(self) -> None:
        with patch.dict(os.environ, {"DEFAULT_NOTIFICATION_CHANNEL": "mock"}, clear=False):
            summary = self.runner.run("capacity_warning", self._context(RUN_MODE_APPLY))

        self.assertEqual(summary.script_run.run_status, "success")
        self.assertEqual(summary.inserted_tasks_count, 2)
        self.assertEqual(summary.inserted_review_tasks_count, 2)
        self.assertEqual(summary.inserted_notification_logs_count, 2)
        self.assertEqual(len(self.repository.list_tasks()), 2)
        self.assertEqual(len(self.repository.list_review_tasks()), 2)
        self.assertEqual(len(self.repository.list_notification_logs()), 2)

    def test_apply_exposes_notification_failures_in_summary(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DEFAULT_NOTIFICATION_CHANNEL": "feishu",
                "FEISHU_WEBHOOK_URL": "https://example.invalid/open-apis/bot/v2/hook/unit-test",
            },
            clear=True,
        ):
            summary = self.runner.run("capacity_warning", self._context(RUN_MODE_APPLY))

        self.assertEqual(summary.inserted_review_tasks_count, 2)
        self.assertTrue(summary.warnings)
        self.assertTrue(any("notification_errors" in warning for warning in summary.warnings))
        items = self.repository.list_script_run_items(summary.script_run.script_run_id)
        self.assertTrue(any(item.error_message for item in items))
        self.assertTrue(all(log.send_status == "failed" for log in self.repository.list_notification_logs()))

    def test_apply_is_idempotent_by_proposal_dedupe_key(self) -> None:
        with patch.dict(os.environ, {"DEFAULT_NOTIFICATION_CHANNEL": "mock"}, clear=False):
            first = self.runner.run("capacity_warning", self._context(RUN_MODE_APPLY))
            second = self.runner.run("capacity_warning", self._context(RUN_MODE_APPLY))

        self.assertEqual(first.inserted_review_tasks_count, 2)
        self.assertEqual(second.inserted_tasks_count, 0)
        self.assertEqual(second.inserted_review_tasks_count, 0)
        self.assertEqual(second.inserted_notification_logs_count, 0)
        items = self.repository.list_script_run_items(second.script_run.script_run_id)
        self.assertTrue(all(item.item_status == "skipped" for item in items))
        self.assertTrue(all(item.decision_trace.get("skip_reason") == "dedupe_key_already_applied" for item in items))
        self.assertEqual(len(self.repository.list_review_tasks()), 2)

    def test_missing_inputs_records_skipped_preview(self) -> None:
        context = EvaluationContext(
            trade_date=self.trade_date,
            runtime_db_path=self.db_path,
            run_mode=RUN_MODE_DRY_RUN,
            now=datetime(2026, 5, 7, 10, 0),
            harvest_forecasts_path=Path(self.temp_dir.name) / "missing_harvest.xlsx",
            capacity_plan_path=self.capacity_path,
            cold_storage_status_path=self.cold_storage_path,
            products_path=self.products_path,
            listing_rules_path=self.listing_rules_path,
            created_by="test",
        )
        summary = self.runner.run("capacity_warning", context)

        self.assertEqual(summary.proposals_count, 1)
        items = self.repository.list_script_run_items(summary.script_run.script_run_id)
        self.assertEqual(items[0].proposal_type, "skipped")
        self.assertIn("skip_reason", items[0].decision_trace)

    def test_empty_capacity_plan_records_skipped_preview(self) -> None:
        _write_workbook(self.capacity_path, CAPACITY_PLAN_HEADERS, [])

        summary = self.runner.run("capacity_warning", self._context(RUN_MODE_DRY_RUN))

        self.assertEqual(summary.script_run.run_status, "success")
        items = self.repository.list_script_run_items(summary.script_run.script_run_id)
        self.assertEqual(items[0].proposal_type, "skipped")
        self.assertEqual(items[0].decision_trace["skip_reason"], "missing_capacity_plan")

    def test_capacity_evaluator_uses_confirmed_capacity_as_final_threshold(self) -> None:
        _write_workbook(
            self.capacity_path,
            CAPACITY_PLAN_HEADERS,
            [[self.trade_date, 250, 100, 0, 500, "proportional_by_forecast", True, "confirmed by operator"]],
        )

        summary = self.runner.run("capacity_warning", self._context(RUN_MODE_DRY_RUN))

        self.assertEqual(summary.proposals_count, 1)
        items = self.repository.list_script_run_items(summary.script_run.script_run_id)
        self.assertEqual(items[0].proposal_type, "skipped")
        self.assertEqual(items[0].decision_trace["skip_reason"], "capacity_within_confirmed_limit")

    def test_listing_rule_evaluator_dry_run_does_not_write_business_tables(self) -> None:
        summary = self.runner.run("listing_rules", self._context(RUN_MODE_DRY_RUN))

        self.assertEqual(summary.proposals_count, 1)
        self.assertEqual(summary.inserted_tasks_count, 0)
        self.assertEqual(self.repository.list_tasks(), [])
        self.assertEqual(self.repository.list_review_tasks(), [])
        items = self.repository.list_script_run_items(summary.script_run.script_run_id)
        self.assertEqual(items[0].item_status, "previewed")
        self.assertEqual(items[0].payload["internal_sku"], "SKU-LOW")

    def test_listing_rule_evaluator_apply_is_idempotent(self) -> None:
        with patch.dict(os.environ, {"DEFAULT_NOTIFICATION_CHANNEL": "mock"}, clear=False):
            first = self.runner.run("listing_rules", self._context(RUN_MODE_APPLY))
            second = self.runner.run("listing_rules", self._context(RUN_MODE_APPLY))

        self.assertEqual(first.inserted_tasks_count, 1)
        self.assertEqual(first.inserted_review_tasks_count, 1)
        self.assertEqual(first.inserted_notification_logs_count, 1)
        self.assertEqual(second.inserted_tasks_count, 0)
        self.assertEqual(second.inserted_review_tasks_count, 0)
        items = self.repository.list_script_run_items(second.script_run.script_run_id)
        self.assertEqual(items[0].item_status, "skipped")
        self.assertEqual(items[0].decision_trace["skip_reason"], "dedupe_key_already_applied")
        self.assertEqual(len(self.repository.list_review_tasks()), 1)

    def test_cold_storage_evaluator_skips_when_capacity_is_ok(self) -> None:
        summary = self.runner.run("cold_storage", self._context(RUN_MODE_DRY_RUN))

        self.assertEqual(summary.proposals_count, 1)
        items = self.repository.list_script_run_items(summary.script_run.script_run_id)
        self.assertEqual(items[0].proposal_type, "skipped")
        self.assertEqual(items[0].decision_trace["skip_reason"], "cold_storage_within_limit")

    def test_cold_storage_evaluator_warns_when_remaining_capacity_is_low(self) -> None:
        _write_workbook(
            self.cold_storage_path,
            COLD_STORAGE_STATUS_HEADERS,
            [[self.trade_date, 500, 430, 40, 0, 50, 470, 30, True, "low remaining"]],
        )

        summary = self.runner.run("cold_storage", self._context(RUN_MODE_DRY_RUN))

        self.assertEqual(summary.proposals_count, 1)
        items = self.repository.list_script_run_items(summary.script_run.script_run_id)
        self.assertEqual(items[0].proposal_type, "review_task")
        self.assertEqual(items[0].severity, "warning")
        self.assertEqual(items[0].decision_trace["rule"], "remaining_capacity_lte_warning_threshold")
        self.assertEqual(items[0].payload["action_type"], "cold_storage_warning")

    def test_cold_storage_evaluator_blocks_capacity_exceeded_as_critical_review(self) -> None:
        _write_workbook(
            self.cold_storage_path,
            COLD_STORAGE_STATUS_HEADERS,
            [[self.trade_date, 500, 480, 50, 0, 50, 530, -30, True, "over capacity"]],
        )

        summary = self.runner.run("cold_storage", self._context(RUN_MODE_DRY_RUN))

        self.assertEqual(summary.proposals_count, 1)
        items = self.repository.list_script_run_items(summary.script_run.script_run_id)
        self.assertEqual(items[0].proposal_type, "review_task")
        self.assertEqual(items[0].severity, "critical")
        self.assertEqual(items[0].decision_trace["rule"], "projected_occupied_exceeds_total_capacity")

    def test_cold_storage_apply_creates_review_and_is_idempotent(self) -> None:
        _write_workbook(
            self.cold_storage_path,
            COLD_STORAGE_STATUS_HEADERS,
            [[self.trade_date, 500, 430, 40, 0, 50, 470, 30, True, "low remaining"]],
        )
        with patch.dict(os.environ, {"DEFAULT_NOTIFICATION_CHANNEL": "mock"}, clear=False):
            first = self.runner.run("cold_storage", self._context(RUN_MODE_APPLY))
            second = self.runner.run("cold_storage", self._context(RUN_MODE_APPLY))

        self.assertEqual(first.inserted_tasks_count, 1)
        self.assertEqual(first.inserted_review_tasks_count, 1)
        self.assertEqual(first.inserted_notification_logs_count, 1)
        self.assertEqual(second.inserted_tasks_count, 0)
        self.assertEqual(second.inserted_review_tasks_count, 0)
        items = self.repository.list_script_run_items(second.script_run.script_run_id)
        self.assertEqual(items[0].item_status, "skipped")
        self.assertEqual(items[0].decision_trace["skip_reason"], "dedupe_key_already_applied")

    def test_empty_cold_storage_status_records_skipped_preview(self) -> None:
        _write_workbook(self.cold_storage_path, COLD_STORAGE_STATUS_HEADERS, [])

        summary = self.runner.run("cold_storage", self._context(RUN_MODE_DRY_RUN))

        self.assertEqual(summary.script_run.run_status, "success")
        items = self.repository.list_script_run_items(summary.script_run.script_run_id)
        self.assertEqual(items[0].proposal_type, "skipped")
        self.assertEqual(items[0].decision_trace["skip_reason"], "missing_cold_storage_status")


if __name__ == "__main__":
    unittest.main()

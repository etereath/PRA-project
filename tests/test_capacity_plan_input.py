from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from app.repositories.workbook_repository import CAPACITY_PLAN_HEADERS, load_capacity_plans
from app.services.capacity_plan_input import (
    CapacityPlanInputError,
    apply_capacity_plan_edit,
    apply_capacity_plan_input,
    load_capacity_plan_input_rows,
    persist_capacity_plan_rows,
    validate_capacity_plan_form,
)


class CapacityPlanInputTests(unittest.TestCase):
    def _write_capacity_rows(self, path: Path, rows: list[list[object]]) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "data"
        sheet.append(CAPACITY_PLAN_HEADERS)
        for row in rows:
            sheet.append(row)
        workbook.save(path)

    def test_add_capacity_plan_saves_confirmed_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "capacity_plans.xlsx"
            self._write_capacity_rows(path, [])

            rows = load_capacity_plan_input_rows(path)
            form = validate_capacity_plan_form(
                {
                    "trade_date": "2026-05-08",
                    "normal_packing_capacity_qty": "250",
                    "confirmed_temp_worker_count": "2",
                    "temp_worker_capacity_qty": "100",
                    "confirmed_packing_capacity_qty": "460",
                    "active": "true",
                    "note": "manual confirmed",
                },
                existing_rows=rows,
                is_edit=False,
            )
            result = apply_capacity_plan_input(rows, form)
            persist_capacity_plan_rows(path, result.rows)

            plans = load_capacity_plans(path)
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0].confirmed_packing_capacity_qty, 460)
            self.assertTrue(plans[0].active)

    def test_default_confirmed_capacity_is_computed(self) -> None:
        rows: list[dict[str, object]] = []
        form = validate_capacity_plan_form(
            {
                "trade_date": "2026-05-08",
                "normal_packing_capacity_qty": "250",
                "confirmed_temp_worker_count": "2",
                "temp_worker_capacity_qty": "100",
                "confirmed_packing_capacity_qty": "",
                "active": "true",
            },
            existing_rows=rows,
            is_edit=False,
        )

        self.assertEqual(form.confirmed_packing_capacity_qty, 450)

    def test_invalid_values_are_rejected(self) -> None:
        rows: list[dict[str, object]] = []
        invalid_forms = [
            ({"trade_date": "bad-date"}, "业务日期格式不正确"),
            (
                {
                    "trade_date": "2026-05-08",
                    "normal_packing_capacity_qty": "-1",
                },
                "基础包装产能",
            ),
            (
                {
                    "trade_date": "2026-05-08",
                    "confirmed_temp_worker_count": "1.5",
                },
                "临时工人数",
            ),
        ]
        for form_values, expected_message in invalid_forms:
            with self.subTest(form_values=form_values):
                with self.assertRaisesRegex(CapacityPlanInputError, expected_message):
                    validate_capacity_plan_form(form_values, existing_rows=rows, is_edit=False)

    def test_duplicate_active_trade_date_is_rejected(self) -> None:
        rows = [
            {
                "trade_date": "2026-05-08",
                "normal_packing_capacity_qty": 250,
                "temp_worker_capacity_qty": 100,
                "confirmed_temp_worker_count": 0,
                "confirmed_packing_capacity_qty": 250,
                "allocation_rule": "proportional_by_forecast",
                "active": True,
                "note": "",
            }
        ]

        with self.assertRaisesRegex(CapacityPlanInputError, "同一业务日期已经存在启用"):
            validate_capacity_plan_form(
                {
                    "trade_date": "2026-05-08",
                    "normal_packing_capacity_qty": "250",
                    "confirmed_temp_worker_count": "0",
                    "temp_worker_capacity_qty": "100",
                    "confirmed_packing_capacity_qty": "250",
                    "active": "true",
                },
                existing_rows=rows,
                is_edit=False,
            )

    def test_edit_capacity_plan_rejects_duplicate_active_date(self) -> None:
        rows = [
            {
                "trade_date": "2026-05-08",
                "normal_packing_capacity_qty": 250,
                "temp_worker_capacity_qty": 100,
                "confirmed_temp_worker_count": 0,
                "confirmed_packing_capacity_qty": 250,
                "allocation_rule": "proportional_by_forecast",
                "active": True,
                "note": "",
            },
            {
                "trade_date": "2026-05-09",
                "normal_packing_capacity_qty": 250,
                "temp_worker_capacity_qty": 100,
                "confirmed_temp_worker_count": 0,
                "confirmed_packing_capacity_qty": 250,
                "allocation_rule": "proportional_by_forecast",
                "active": True,
                "note": "",
            },
        ]

        with self.assertRaisesRegex(CapacityPlanInputError, "同一业务日期已经存在启用"):
            validate_capacity_plan_form(
                {
                    "current_row_index": "1",
                    "current_trade_date": "2026-05-09",
                    "trade_date": "2026-05-08",
                    "normal_packing_capacity_qty": "250",
                    "confirmed_temp_worker_count": "0",
                    "temp_worker_capacity_qty": "100",
                    "confirmed_packing_capacity_qty": "250",
                    "active": "true",
                },
                existing_rows=rows,
                is_edit=True,
            )

    def test_edit_capacity_plan_updates_selected_row(self) -> None:
        rows = [
            {
                "trade_date": "2026-05-08",
                "normal_packing_capacity_qty": 250,
                "temp_worker_capacity_qty": 100,
                "confirmed_temp_worker_count": 0,
                "confirmed_packing_capacity_qty": 250,
                "allocation_rule": "proportional_by_forecast",
                "active": True,
                "note": "",
            }
        ]
        form = validate_capacity_plan_form(
            {
                "current_row_index": "0",
                "current_trade_date": "2026-05-08",
                "trade_date": "2026-05-08",
                "normal_packing_capacity_qty": "300",
                "confirmed_temp_worker_count": "1",
                "temp_worker_capacity_qty": "100",
                "confirmed_packing_capacity_qty": "410",
                "active": "true",
            },
            existing_rows=rows,
            is_edit=True,
        )

        result = apply_capacity_plan_edit(rows, "2026-05-08", form, 0)

        self.assertEqual(result.rows[0]["normal_packing_capacity_qty"], 300)
        self.assertEqual(result.rows[0]["confirmed_packing_capacity_qty"], 410)


if __name__ == "__main__":
    unittest.main()

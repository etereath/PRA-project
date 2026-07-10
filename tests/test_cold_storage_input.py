from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from app.repositories.workbook_repository import COLD_STORAGE_STATUS_HEADERS, load_cold_storage_statuses, save_table_records
from app.services.cold_storage_input import (
    ColdStorageInputError,
    apply_cold_storage_edit,
    apply_cold_storage_input,
    computed_projected_occupied_from_row,
    computed_remaining_capacity_from_row,
    load_cold_storage_input_rows,
    persist_cold_storage_rows,
    validate_cold_storage_form,
)


def _write_workbook(path: Path, rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"
    sheet.append(COLD_STORAGE_STATUS_HEADERS)
    for row in rows:
        sheet.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


class ColdStorageInputTests(unittest.TestCase):
    def test_add_cold_storage_status_and_save_to_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cold_storage_status.xlsx"
            _write_workbook(path, [])
            rows = load_cold_storage_input_rows(path)
            form = validate_cold_storage_form(
                {
                    "trade_date": "2026-05-08",
                    "total_capacity_qty": "500",
                    "current_occupied_qty": "120",
                    "expected_inbound_qty": "80",
                    "expected_outbound_qty": "20",
                    "warning_threshold_qty": "50",
                    "projected_occupied_qty": "",
                    "remaining_capacity_qty": "",
                    "active": "true",
                    "note": "Web 表单录入",
                },
                existing_rows=rows,
                is_edit=False,
            )
            result = apply_cold_storage_input(rows, form)
            persist_cold_storage_rows(path, result.rows)

            statuses = load_cold_storage_statuses(path)
            self.assertEqual(len(statuses), 1)
            self.assertEqual(statuses[0].trade_date.isoformat(), "2026-05-08")
            self.assertEqual(statuses[0].projected_occupied_qty, 180)
            self.assertEqual(statuses[0].remaining_capacity_qty, 320)

    def test_edit_cold_storage_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cold_storage_status.xlsx"
            save_table_records(
                "cold_storage_status",
                path,
                [
                    dict(
                        zip(
                            COLD_STORAGE_STATUS_HEADERS,
                            ["2026-05-08", 500, 120, 0, 0, 50, 120, 380, True, ""],
                            strict=True,
                        )
                    )
                ],
            )
            rows = load_cold_storage_input_rows(path)
            form = validate_cold_storage_form(
                {
                    "current_row_index": "0",
                    "current_trade_date": "2026-05-08",
                    "trade_date": "2026-05-08",
                    "total_capacity_qty": "500",
                    "current_occupied_qty": "300",
                    "expected_inbound_qty": "0",
                    "expected_outbound_qty": "50",
                    "warning_threshold_qty": "50",
                    "active": "true",
                    "note": "updated",
                },
                existing_rows=rows,
                is_edit=True,
            )
            result = apply_cold_storage_edit(rows, "2026-05-08", form, 0)
            persist_cold_storage_rows(path, result.rows)

            statuses = load_cold_storage_statuses(path)
            self.assertEqual(statuses[0].projected_occupied_qty, 250)
            self.assertEqual(statuses[0].remaining_capacity_qty, 250)
            self.assertEqual(statuses[0].note, "updated")

    def test_duplicate_active_trade_date_is_rejected(self) -> None:
        existing = [
            {
                "trade_date": "2026-05-08",
                "total_capacity_qty": 500,
                "current_occupied_qty": 100,
                "expected_inbound_qty": 0,
                "expected_outbound_qty": 0,
                "warning_threshold_qty": 50,
                "projected_occupied_qty": 100,
                "remaining_capacity_qty": 400,
                "active": True,
                "note": "",
            }
        ]
        with self.assertRaisesRegex(ColdStorageInputError, "同一业务日期已经存在启用"):
            validate_cold_storage_form(
                {
                    "trade_date": "2026-05-08",
                    "total_capacity_qty": "500",
                    "current_occupied_qty": "120",
                    "expected_inbound_qty": "0",
                    "expected_outbound_qty": "0",
                    "warning_threshold_qty": "50",
                    "active": "true",
                },
                existing_rows=existing,
                is_edit=False,
            )

    def test_invalid_values_are_rejected(self) -> None:
        rows: list[dict[str, object]] = []
        invalid_forms = [
            ({"trade_date": "", "total_capacity_qty": "500"}, "请选择业务日期"),
            ({"trade_date": "2026-05-08", "total_capacity_qty": "-1"}, "冷库总容量"),
            ({"trade_date": "2026-05-08", "current_occupied_qty": "-1"}, "当前占用量"),
            ({"trade_date": "2026-05-08", "expected_inbound_qty": "-1"}, "预计入库量"),
            ({"trade_date": "2026-05-08", "expected_outbound_qty": "-1"}, "预计出库量"),
            ({"trade_date": "2026-05-08", "warning_threshold_qty": "-1"}, "预警阈值"),
            ({"trade_date": "2026-05-08", "active": "maybe"}, "请选择是否启用"),
        ]
        for form, expected_message in invalid_forms:
            with self.subTest(form=form):
                with self.assertRaisesRegex(ColdStorageInputError, expected_message):
                    validate_cold_storage_form(form, existing_rows=rows, is_edit=False)

    def test_computed_values_from_row(self) -> None:
        row = {
            "total_capacity_qty": 500,
            "current_occupied_qty": 120,
            "expected_inbound_qty": 80,
            "expected_outbound_qty": 20,
            "warning_threshold_qty": 50,
        }
        self.assertEqual(computed_projected_occupied_from_row(row), 180)
        self.assertEqual(computed_remaining_capacity_from_row(row), 320)


if __name__ == "__main__":
    unittest.main()

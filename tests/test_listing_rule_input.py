from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from app.repositories.workbook_repository import LISTING_RULE_HEADERS, load_listing_rules
from app.services.listing_rule_input import (
    ListingRuleInputError,
    apply_listing_rule_edit,
    apply_listing_rule_input,
    format_listing_rule_scope,
    load_listing_rule_input_rows,
    persist_listing_rule_rows,
    validate_listing_rule_form,
)


def _write_workbook(path: Path, rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"
    sheet.append(LISTING_RULE_HEADERS)
    for row in rows:
        sheet.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


class ListingRuleInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "listing_rules.xlsx"
        _write_workbook(
            self.path,
            [["LIST-LOW", "库存不足下架", "*", "*", "*", 0, "stock_below_offline", True, 1, ""]],
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_add_listing_rule_saves_new_three_dimensional_filters(self) -> None:
        rows = load_listing_rule_input_rows(self.path)
        form = validate_listing_rule_form(
            {
                "rule_name": "艾莎B级蚂蚁允许上架",
                "variety_filter": "艾莎",
                "grade_filter": "b",
                "platform_filter": "蚂蚁",
                "stock_threshold": "10",
                "listing_strategy": "stock_above_online",
                "active": "是",
                "priority": "5",
                "remark": "test",
            },
            existing_rows=rows,
            is_edit=False,
            allowed_varieties=["艾莎"],
            allowed_platforms=["蚂蚁"],
        )
        result = apply_listing_rule_input(rows, form)
        persist_listing_rule_rows(self.path, result.rows)

        saved_rows = load_listing_rule_input_rows(self.path)
        self.assertEqual(saved_rows[-1]["variety_filter"], "艾莎")
        self.assertEqual(saved_rows[-1]["grade_filter"], "B")
        self.assertEqual(saved_rows[-1]["platform_filter"], "蚂蚁")
        self.assertEqual(saved_rows[-1]["listing_strategy"], "stock_above_online")
        self.assertEqual(format_listing_rule_scope(saved_rows[-1]), "艾莎 / B级 / 蚂蚁")
        self.assertEqual(len(load_listing_rules(self.path)), 2)

    def test_edit_listing_rule_and_wildcard_scope_roundtrip(self) -> None:
        rows = load_listing_rule_input_rows(self.path)
        form = validate_listing_rule_form(
            {
                "rule_id": "LIST-LOW",
                "rule_name": "全部商品库存不足下架",
                "variety_filter": "*",
                "grade_filter": "*",
                "platform_filter": "*",
                "stock_threshold": "1",
                "listing_strategy": "stock_below_offline",
                "active": "否",
                "priority": "2",
                "remark": "",
            },
            existing_rows=rows,
            is_edit=True,
            allowed_varieties=["艾莎"],
            allowed_platforms=["蚂蚁"],
        )
        result = apply_listing_rule_edit(rows, form)
        persist_listing_rule_rows(self.path, result.rows)
        saved = load_listing_rule_input_rows(self.path)[0]
        self.assertEqual(saved["variety_filter"], "*")
        self.assertEqual(saved["grade_filter"], "*")
        self.assertEqual(saved["platform_filter"], "*")
        self.assertEqual(format_listing_rule_scope(saved), "全部商品")

    def test_direct_set_offline_strategy_is_saved_and_loadable(self) -> None:
        rows = load_listing_rule_input_rows(self.path)
        form = validate_listing_rule_form(
            {
                "rule_name": "蚂蚁艾莎A直接下架",
                "variety_filter": "艾莎",
                "grade_filter": "A",
                "platform_filter": "蚂蚁",
                "stock_threshold": "0",
                "listing_strategy": "set_offline",
                "active": "是",
                "priority": "1",
                "remark": "人工配置的直接下架规则",
            },
            existing_rows=rows,
            is_edit=False,
            allowed_varieties=["艾莎"],
            allowed_platforms=["蚂蚁"],
        )
        result = apply_listing_rule_input(rows, form)
        persist_listing_rule_rows(self.path, result.rows)

        saved_rows = load_listing_rule_input_rows(self.path)
        self.assertEqual(saved_rows[-1]["listing_strategy"], "set_offline")
        loaded_rules = load_listing_rules(self.path)
        self.assertEqual(loaded_rules[-1].listing_strategy.value, "set_offline")

    def test_invalid_values_are_rejected_for_operator_friendly_reasons(self) -> None:
        rows = load_listing_rule_input_rows(self.path)
        cases = [
            ("variety_filter", "不存在品种", "请选择当前商品资料中已有的品种"),
            ("grade_filter", "Z", "等级筛选必须选择"),
            ("platform_filter", "未知平台", "请选择有效的平台"),
            ("stock_threshold", "-1", "库存阈值必须大于或等于 0"),
            ("priority", "-1", "优先级必须大于或等于 0"),
        ]
        base = {
            "rule_name": "测试规则",
            "variety_filter": "*",
            "grade_filter": "*",
            "platform_filter": "*",
            "stock_threshold": "0",
            "listing_strategy": "stock_below_offline",
            "active": "是",
            "priority": "1",
        }
        for field_name, bad_value, message in cases:
            values = dict(base)
            values[field_name] = bad_value
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ListingRuleInputError, message):
                    validate_listing_rule_form(
                        values,
                        existing_rows=rows,
                        is_edit=False,
                        allowed_varieties=["艾莎"],
                        allowed_platforms=["蚂蚁"],
                    )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from app.enums import PricingMethod, RoundingRule
from app.repositories.workbook_repository import load_price_rules, save_table_records
from app.services.price_rule_input import (
    PriceRuleInputError,
    apply_price_rule_edit,
    apply_price_rule_input,
    load_price_rule_input_rows,
    persist_price_rule_rows,
    validate_price_rule_form,
)


def _save_price_rules(path: Path, rows: list[dict[str, object]]) -> None:
    save_table_records("price_rules", path, rows)


def _rule_row(rule_id: str = "RULE-1", *, active: str = "True") -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "rule_name": "A grade fixed markup",
        "variety_filter": "*",
        "grade_filter": "A",
        "platform_filter": "*",
        "pricing_method": "fixed_markup",
        "markup_value": "5",
        "min_price": "12",
        "rounding_rule": "round",
        "rounding_step": "",
        "active": active,
        "priority": "10",
        "remark": "",
    }


class PriceRuleInputTests(unittest.TestCase):
    def test_add_price_rule_and_keep_workbook_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "price_rules.xlsx"
            _save_price_rules(path, [])

            rows = load_price_rule_input_rows(path)
            form = validate_price_rule_form(
                {
                    "rule_name": "Aisha B percentage markup",
                    "variety_filter": "艾莎",
                    "grade_filter": "b",
                    "platform_filter": "*",
                    "pricing_method": "percentage_markup",
                    "markup_value": "10",
                    "min_price": "8",
                    "rounding_rule": "step",
                    "rounding_step": "0.5",
                    "active": "是",
                    "priority": "20",
                    "remark": "Web input",
                },
                existing_rows=rows,
                is_edit=False,
                allowed_varieties=["艾莎", "卡布奇诺"],
                allowed_platforms=["蚂蚁", "珍情"],
            )
            result = apply_price_rule_input(rows, form)
            persist_price_rule_rows(path, result.rows)

            rules = load_price_rules(path)
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0].variety_filter, "艾莎")
            self.assertEqual(rules[0].grade_filter, "B")
            self.assertEqual(rules[0].platform_filter, "*")
            self.assertEqual(rules[0].pricing_method, PricingMethod.PERCENTAGE_MARKUP)
            self.assertEqual(rules[0].rounding_rule, RoundingRule.STEP)
            self.assertTrue(rules[0].active)

    def test_edit_price_rule(self) -> None:
        rows = [_rule_row()]
        form = validate_price_rule_form(
            {
                "rule_id": "RULE-1",
                "rule_name": "Global fixed markup",
                "variety_filter": "*",
                "grade_filter": "*",
                "platform_filter": "*",
                "pricing_method": "fixed_markup",
                "markup_value": "3",
                "min_price": "",
                "rounding_rule": "none",
                "rounding_step": "",
                "active": "否",
                "priority": "5",
                "remark": "updated",
            },
            existing_rows=rows,
            is_edit=True,
        )
        result = apply_price_rule_edit(rows, form)

        self.assertEqual(result.rows[0]["variety_filter"], "*")
        self.assertEqual(result.rows[0]["grade_filter"], "*")
        self.assertEqual(result.rows[0]["platform_filter"], "*")
        self.assertEqual(result.rows[0]["markup_value"], "3")
        self.assertEqual(result.rows[0]["min_price"], "")
        self.assertEqual(result.rows[0]["active"], "False")
        self.assertEqual(result.rows[0]["priority"], "5")

    def test_invalid_numeric_and_select_values_are_rejected(self) -> None:
        base = {
            "rule_name": "bad rule",
            "variety_filter": "*",
            "grade_filter": "A",
            "platform_filter": "*",
            "pricing_method": "fixed_markup",
            "markup_value": "0",
            "min_price": "",
            "rounding_rule": "round",
            "rounding_step": "",
            "active": "是",
            "priority": "10",
        }
        with self.assertRaises(PriceRuleInputError):
            validate_price_rule_form(base, existing_rows=[], is_edit=False)

        valid_decrease = validate_price_rule_form(
            base | {"markup_value": "-1"},
            existing_rows=[],
            is_edit=False,
        )
        self.assertEqual(valid_decrease.markup_value, Decimal("-1"))

        invalid_method = base | {"markup_value": "1", "pricing_method": "target_price"}
        with self.assertRaises(PriceRuleInputError):
            validate_price_rule_form(invalid_method, existing_rows=[], is_edit=False)

        invalid_active = base | {"markup_value": "1", "active": "maybe"}
        with self.assertRaises(PriceRuleInputError):
            validate_price_rule_form(invalid_active, existing_rows=[], is_edit=False)

    def test_invalid_filter_values_are_rejected(self) -> None:
        base = {
            "rule_name": "bad filter",
            "variety_filter": "*",
            "grade_filter": "A",
            "platform_filter": "*",
            "pricing_method": "fixed_markup",
            "markup_value": "1",
            "min_price": "",
            "rounding_rule": "round",
            "rounding_step": "",
            "active": "true",
            "priority": "10",
        }
        with self.assertRaises(PriceRuleInputError):
            validate_price_rule_form(
                base | {"variety_filter": "不存在品种"},
                existing_rows=[],
                is_edit=False,
                allowed_varieties=["艾莎"],
            )
        with self.assertRaises(PriceRuleInputError):
            validate_price_rule_form(base | {"grade_filter": ""}, existing_rows=[], is_edit=False)
        with self.assertRaises(PriceRuleInputError):
            validate_price_rule_form(
                base | {"platform_filter": "不存在平台"},
                existing_rows=[],
                is_edit=False,
                allowed_platforms=["蚂蚁"],
            )

    def test_step_rounding_requires_positive_step(self) -> None:
        with self.assertRaises(PriceRuleInputError) as context:
            validate_price_rule_form(
                {
                    "rule_name": "step rule",
                    "variety_filter": "*",
                    "grade_filter": "*",
                    "platform_filter": "*",
                    "pricing_method": "fixed_markup",
                    "markup_value": "1",
                    "min_price": "",
                    "rounding_rule": "step",
                    "rounding_step": "",
                    "active": "true",
                    "priority": "10",
                },
                existing_rows=[],
                is_edit=False,
            )
        self.assertIn("取整步长", str(context.exception))


if __name__ == "__main__":
    unittest.main()

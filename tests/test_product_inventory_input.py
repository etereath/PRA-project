from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.exceptions import ValidationError
from app.repositories.workbook_repository import load_products, save_table_records
from app.services.product_inventory_input import (
    ProductInventoryInputError,
    apply_inventory_input,
    apply_product_edit,
    load_product_input_rows,
    persist_product_rows,
    validate_inventory_form,
    validate_product_edit_form,
)


def _save_products(path: Path, rows: list[dict[str, object]]) -> None:
    save_table_records("products", path, rows)


def _product_row(
    sku: str,
    *,
    name: str = "艾莎",
    grade: str = "B",
    stem_length: str = "跟随等级",
    unit: str = "扎",
    stock: int = 5,
    cost: str = "10",
    enabled: str = "True",
) -> dict[str, object]:
    return {
        "internal_sku": sku,
        "product_name": name,
        "grade": grade,
        "stem_length": stem_length,
        "unit": unit,
        "base_cost": cost,
        "current_stock": str(stock),
        "sale_enabled": enabled,
        "last_price": "",
        "recommended_price": "",
        "remark": "",
        "feature_season": "",
        "feature_color": "",
    }


class ProductInventoryInputTests(unittest.TestCase):
    def test_add_new_product_with_variety_code_and_follow_grade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "products.xlsx"
            _save_products(path, [])

            rows = load_product_input_rows(path)
            form = validate_inventory_form(
                {
                    "product_name": "新品种",
                    "variety_code": "NEWROSE",
                    "grade": "0",
                    "stem_length": "跟随等级",
                    "unit": "扎",
                    "base_cost": "8.5",
                    "quantity": "3",
                    "sale_enabled": "是",
                }
            )
            result = apply_inventory_input(rows, form)
            persist_product_rows(path, result.rows)

            products = load_products(path)
            self.assertEqual(len(products), 1)
            self.assertEqual(products[0].internal_sku, "NEWROSE-0-0-Z")
            self.assertEqual(products[0].grade, "0")
            self.assertEqual(products[0].stem_length, "0")
            self.assertEqual(products[0].current_stock, 3)

    def test_unknown_variety_without_code_is_rejected(self) -> None:
        form = validate_inventory_form(
            {
                "product_name": "未维护品种",
                "grade": "B",
                "stem_length": "跟随等级",
                "unit": "扎",
                "base_cost": "8",
                "quantity": "3",
                "sale_enabled": "true",
            }
        )
        with self.assertRaises(ProductInventoryInputError) as context:
            apply_inventory_input([], form)
        self.assertIn("品种代码", str(context.exception))

    def test_same_type_inventory_is_supplemented_after_normalization(self) -> None:
        rows = [_product_row("AISHA-B-FG-Z", stock=5, cost="10", enabled="True")]
        form = validate_inventory_form(
            {
                "product_name": "艾莎 ",
                "grade": "b",
                "stem_length": "FG",
                "unit": "扎",
                "base_cost": "12",
                "quantity": "4",
                "sale_enabled": "否",
            }
        )

        result = apply_inventory_input(rows, form)

        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0]["internal_sku"], "AISHA-B-FG-Z")
        self.assertEqual(result.rows[0]["current_stock"], "9")
        self.assertEqual(result.rows[0]["stem_length"], "60")
        self.assertEqual(result.rows[0]["base_cost"], "12")
        self.assertEqual(result.rows[0]["sale_enabled"], "False")
        self.assertIn("基础成本、是否允许销售更新", result.message)

    def test_follow_grade_resolves_to_grade_stem_length(self) -> None:
        expected = {
            "A": ("65", "TEST-A-65-Z"),
            "B": ("60", "TEST-B-60-Z"),
            "C": ("55", "TEST-C-55-Z"),
            "D": ("50", "TEST-D-50-Z"),
            "E": ("45", "TEST-E-45-Z"),
            "0": ("0", "TEST-0-0-Z"),
        }
        for grade, (stem_length, sku) in expected.items():
            with self.subTest(grade=grade):
                form = validate_inventory_form(
                    {
                        "product_name": "测试品种",
                        "variety_code": "TEST",
                        "grade": grade,
                        "stem_length": "跟随等级",
                        "unit": "扎",
                        "base_cost": "8",
                        "quantity": "1",
                        "sale_enabled": "true",
                    }
                )
                result = apply_inventory_input([], form)
                self.assertEqual(result.rows[0]["stem_length"], stem_length)
                self.assertEqual(result.rows[0]["internal_sku"], sku)

    def test_duplicate_same_type_rows_are_rejected(self) -> None:
        rows = [_product_row("SKU-1"), _product_row("SKU-2")]
        form = validate_inventory_form(
            {
                "product_name": "艾莎",
                "grade": "B",
                "stem_length": "跟随等级",
                "unit": "扎",
                "base_cost": "10",
                "quantity": "1",
                "sale_enabled": "true",
            }
        )
        with self.assertRaises(ProductInventoryInputError):
            apply_inventory_input(rows, form)

    def test_edit_rejects_duplicate_product_type(self) -> None:
        rows = [
            _product_row("SKU-1", name="艾莎", grade="A"),
            _product_row("SKU-2", name="艾莎", grade="B"),
        ]
        form = validate_product_edit_form(
            {
                "internal_sku": "SKU-2",
                "product_name": "艾莎",
                "grade": "A",
                "stem_length": "跟随等级",
                "unit": "扎",
                "base_cost": "10",
                "current_stock": "8",
                "sale_enabled": "true",
            }
        )
        with self.assertRaises(ProductInventoryInputError) as context:
            apply_product_edit(rows, form)
        self.assertIn("重复", str(context.exception))

    def test_invalid_quantity_and_cost_are_rejected(self) -> None:
        with self.assertRaises(ProductInventoryInputError):
            validate_inventory_form(
                {
                    "product_name": "艾莎",
                    "grade": "B",
                    "stem_length": "跟随等级",
                    "unit": "扎",
                    "base_cost": "-1",
                    "quantity": "0",
                    "sale_enabled": "true",
                }
            )

    def test_db_authority_rejects_legacy_workbook_inventory_input(self) -> None:
        form = validate_inventory_form(
            {
                "product_name": "艾莎",
                "grade": "A",
                "stem_length": "跟随等级",
                "unit": "扎",
                "base_cost": "10",
                "quantity": "8",
                "sale_enabled": "true",
            }
        )
        with self.assertRaises(ProductInventoryInputError) as context:
            apply_inventory_input(
                [_product_row("SKU-1", name="艾莎", grade="A")],
                form,
                inventory_authoritative=True,
            )
        self.assertIn("唯一权威", str(context.exception))

    def test_db_authority_product_edit_cannot_change_workbook_stock(self) -> None:
        form = validate_product_edit_form(
            {
                "internal_sku": "SKU-1",
                "product_name": "艾莎",
                "grade": "A",
                "stem_length": "跟随等级",
                "unit": "扎",
                "base_cost": "10",
                "current_stock": "99",
                "sale_enabled": "true",
            }
        )
        with self.assertRaises(ProductInventoryInputError) as context:
            apply_product_edit(
                [_product_row("SKU-1", name="艾莎", grade="A")],
                form,
                inventory_authoritative=True,
            )
        self.assertIn("不能修改", str(context.exception))


if __name__ == "__main__":
    unittest.main()

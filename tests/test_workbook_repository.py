from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from app.exceptions import TableValidationError, ValidationError
from app.repositories.workbook_repository import PRODUCT_HEADERS, load_products, load_table_records, save_table_records


class WorkbookRepositoryTests(unittest.TestCase):
    def test_duplicate_internal_sku_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "products.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(PRODUCT_HEADERS)
            row = ["SKU-001", "rose", "A", "60cm", "bundle", 10, 1, True, 18, 19, "", "spring", "red"]
            sheet.append(row)
            sheet.append(row)
            workbook.save(path)

            with self.assertRaises(ValidationError):
                load_products(path)

    def test_save_and_load_table_records_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "products.xlsx"
            rows = [
                {
                    "internal_sku": "SKU-001",
                    "product_name": "rose",
                    "grade": "A",
                    "stem_length": "60cm",
                    "unit": "bundle",
                    "base_cost": "10",
                    "current_stock": "5",
                    "sale_enabled": "true",
                    "last_price": "18",
                    "recommended_price": "19",
                    "remark": "",
                    "feature_season": "spring",
                    "feature_color": "red",
                }
            ]
            save_table_records("products", path, rows)
            loaded = load_table_records("products", path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["internal_sku"], "SKU-001")

    def test_save_table_records_raises_field_level_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "products.xlsx"
            rows = [
                {
                    "internal_sku": "SKU-001",
                    "product_name": "",
                    "grade": "A",
                    "stem_length": "60cm",
                    "unit": "bundle",
                    "base_cost": "abc",
                    "current_stock": "five",
                    "sale_enabled": "maybe",
                    "last_price": "",
                    "recommended_price": "x",
                    "remark": "",
                    "feature_season": "spring",
                    "feature_color": "red",
                }
            ]
            with self.assertRaises(TableValidationError) as context:
                save_table_records("products", path, rows)

            issues = {(item.field_name, item.message) for item in context.exception.issues}
            self.assertIn(("product_name", "该字段必填"), issues)
            self.assertIn(("base_cost", "请输入数字"), issues)
            self.assertIn(("current_stock", "请输入整数"), issues)
            self.assertIn(("sale_enabled", "请输入 true/false、1/0、yes/no"), issues)
            self.assertIn(("recommended_price", "请输入数字"), issues)


if __name__ == "__main__":
    unittest.main()

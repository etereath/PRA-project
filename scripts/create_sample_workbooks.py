from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openpyxl import Workbook

from app.repositories.workbook_repository import LISTING_RULE_HEADERS, PRICE_RULE_HEADERS, PRODUCT_HEADERS

SAMPLES_DIR = ROOT / "data" / "samples"
TEMPLATES_DIR = ROOT / "data" / "templates"


def write_workbook(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def main() -> None:
    write_workbook(
        TEMPLATES_DIR / "products_template.xlsx",
        PRODUCT_HEADERS,
        [],
    )
    write_workbook(
        TEMPLATES_DIR / "price_rules_template.xlsx",
        PRICE_RULE_HEADERS,
        [],
    )
    write_workbook(
        TEMPLATES_DIR / "listing_rules_template.xlsx",
        LISTING_RULE_HEADERS,
        [],
    )

    write_workbook(
        SAMPLES_DIR / "products.xlsx",
        PRODUCT_HEADERS,
        [
            ["SKU-001", "红色月季A级", "rose", "A", "60cm", "bundle", 10, 50, True, 18, "normal stock", "spring", "red"],
            ["SKU-002", "白色月季B级", "rose", "B", "50cm", "bundle", 8, 0, True, 15, "out of stock", "spring", "white"],
            ["SKU-003", "粉色月季A级", "rose", "A", "70cm", "bundle", 12, 12, False, 20, "sale disabled", "summer", "pink"],
        ],
    )
    write_workbook(
        SAMPLES_DIR / "price_rules.xlsx",
        PRICE_RULE_HEADERS,
        [
            ["RULE-ALL-1", "全局固定加价", "all", "*", "fixed_markup", 5, 14, "round", "", True, 10, ""],
            ["RULE-ROSE-A", "A级品种加价10%", "grade", "A", "percentage_markup", 10, "", "step", 0.5, True, 20, ""],
        ],
    )
    write_workbook(
        SAMPLES_DIR / "listing_rules.xlsx",
        LISTING_RULE_HEADERS,
        [
            ["LIST-LOW", "库存小于等于0下架", "stock_lte", 0, "set_offline", True, 1, ""],
            ["LIST-RESTOCK", "库存大于等于10上架", "stock_gte", 10, "set_online", True, 5, ""],
        ],
    )
    print("Sample and template workbooks created.")


if __name__ == "__main__":
    main()

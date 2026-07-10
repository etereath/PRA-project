from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.repositories.workbook_repository import LISTING_RULE_HEADERS, PRICE_RULE_HEADERS, PRODUCT_HEADERS
from app.repositories.workbook_repository import (
    CAPACITY_PLAN_HEADERS,
    COLD_STORAGE_STATUS_HEADERS,
    HARVEST_FORECAST_HEADERS,
    PRICE_FORECAST_HEADERS,
)

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
    write_workbook(TEMPLATES_DIR / "products_template.xlsx", PRODUCT_HEADERS, [])
    write_workbook(TEMPLATES_DIR / "price_rules_template.xlsx", PRICE_RULE_HEADERS, [])
    write_workbook(TEMPLATES_DIR / "listing_rules_template.xlsx", LISTING_RULE_HEADERS, [])
    write_workbook(TEMPLATES_DIR / "harvest_forecasts_template.xlsx", HARVEST_FORECAST_HEADERS, [])
    write_workbook(TEMPLATES_DIR / "price_forecasts_template.xlsx", PRICE_FORECAST_HEADERS, [])
    write_workbook(TEMPLATES_DIR / "capacity_plans_template.xlsx", CAPACITY_PLAN_HEADERS, [])
    write_workbook(TEMPLATES_DIR / "cold_storage_status_template.xlsx", COLD_STORAGE_STATUS_HEADERS, [])

    write_workbook(
        SAMPLES_DIR / "products.xlsx",
        PRODUCT_HEADERS,
        [
            ["SKU-001", "艾莎", "A", "70", "扎", 10, 50, True, 14, 14, "normal stock", "spring", "red"],
            ["SKU-002", "艾莎", "B", "60", "扎", 10, 0, True, 13, 13, "out of stock", "spring", "white"],
            ["SKU-003", "卡布奇诺", "A", "70", "扎", 10, 12, False, 12, 12, "sale disabled", "summer", "pink"],
        ],
    )
    write_workbook(
        SAMPLES_DIR / "price_rules.xlsx",
        PRICE_RULE_HEADERS,
        [
            ["RULE-ALL-1", "全局固定加价", "*", "*", "*", "fixed_markup", 5, 14, "round", "", True, 10, ""],
            ["RULE-ROSE-A", "A级加价10%", "*", "A", "*", "percentage_markup", 10, "", "step", 0.5, True, 20, ""],
        ],
    )
    write_workbook(
        SAMPLES_DIR / "listing_rules.xlsx",
        LISTING_RULE_HEADERS,
        [
            ["LIST-LOW", "库存不足下架", "*", "*", "*", 0, "stock_below_offline", True, 1, ""],
            ["LIST-RESTOCK", "库存恢复允许上架", "*", "*", "*", 10, "stock_above_online", True, 5, ""],
        ],
    )
    write_workbook(
        SAMPLES_DIR / "harvest_forecasts.xlsx",
        HARVEST_FORECAST_HEADERS,
        [
            ["HF-001", "2026-05-03", "2026-05-04", "鑹捐帋", "A", 180, 150, 210, "0.80", "manual", "2026-05-03T16:00:00", ""],
            ["HF-002", "2026-05-03", "2026-05-04", "鑹捐帋", "B", 120, 90, 150, "0.75", "manual", "2026-05-03T16:00:00", ""],
            ["HF-003", "2026-05-03", "2026-05-04", "鍗″竷濂囪", "A", 80, 60, 100, "0.70", "manual", "2026-05-03T16:00:00", ""],
        ],
    )
    write_workbook(
        SAMPLES_DIR / "price_forecasts.xlsx",
        PRICE_FORECAST_HEADERS,
        [
            ["PF-001", "2026-05-03", "2026-05-04", "鑹捐帋", "A", 16, 14, 20, "0.80", "manual", "2026-05-03T16:00:00", ""],
            ["PF-002", "2026-05-03", "2026-05-04", "鑹捐帋", "B", 13, 11, 16, "0.75", "manual", "2026-05-03T16:00:00", ""],
            ["PF-003", "2026-05-03", "2026-05-04", "鍗″竷濂囪", "A", 15, 12, 18, "0.70", "manual", "2026-05-03T16:00:00", ""],
        ],
    )
    write_workbook(
        SAMPLES_DIR / "capacity_plans.xlsx",
        CAPACITY_PLAN_HEADERS,
        [["2026-05-04", 250, 100, 0, 250, "proportional_by_forecast", True, "default sample"]],
    )
    write_workbook(
        SAMPLES_DIR / "cold_storage_status.xlsx",
        COLD_STORAGE_STATUS_HEADERS,
        [["2026-05-04", 500, 120, 0, 0, 50, 120, 380, True, "default sample"]],
    )
    print("Sample and template workbooks created.")


if __name__ == "__main__":
    main()

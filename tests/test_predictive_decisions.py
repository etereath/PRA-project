from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from app.enums import PricingSource, ShortageRisk, TaskActionType, TradePhase
from app.models import HarvestForecast, PackingCapacityPlan, PriceForecast, Product
from app.repositories.workbook_repository import (
    HARVEST_FORECAST_HEADERS,
    PRICE_FORECAST_HEADERS,
    load_harvest_forecasts,
    load_price_forecasts,
)
from app.services.capacity_planning import CapacityPlanningService
from app.services.inventory_planning import InventoryPlanningService
from app.services.pricing_decision import PricingDecisionService
from app.services.task_generation import TaskGenerationService
from app.services.trade_window import TradeWindowService
from app.services.listing import ListingService
from app.services.pricing import PricingService


def _product(
    sku: str,
    product_name: str = "rose",
    grade: str = "A",
    stock: int = 0,
    sale_enabled: bool = True,
    last_price: Decimal | None = Decimal("20"),
    recommended_price: Decimal | None = Decimal("18"),
) -> Product:
    return Product(
        internal_sku=sku,
        product_name=product_name,
        grade=grade,
        stem_length="60cm",
        unit="bundle",
        base_cost=Decimal("10"),
        current_stock=stock,
        sale_enabled=sale_enabled,
        last_price=last_price,
        recommended_price=recommended_price,
    )


class PredictiveDecisionTests(unittest.TestCase):
    def test_trade_window_phases(self) -> None:
        service = TradeWindowService()
        trade_date = date(2026, 5, 4)

        normal = service.build(trade_date, now=datetime(2026, 5, 4, 15, 29))
        clearance = service.build(trade_date, now=datetime(2026, 5, 4, 15, 30))
        closed = service.build(trade_date, now=datetime(2026, 5, 4, 17, 0))

        self.assertEqual(normal.trade_open_at, datetime(2026, 5, 3, 23, 0))
        self.assertEqual(normal.phase, TradePhase.NORMAL_TRADING)
        self.assertEqual(clearance.phase, TradePhase.CLEARANCE)
        self.assertEqual(closed.phase, TradePhase.CLOSED)

    def test_capacity_allocation_is_shared_across_forecast_groups(self) -> None:
        forecasts = [
            HarvestForecast(
                forecast_id="HF-A",
                forecast_date=date(2026, 5, 3),
                target_trade_date=date(2026, 5, 4),
                forecast_group_key="rose::A",
                variety="rose",
                grade="A",
                predicted_harvest_qty=200,
            ),
            HarvestForecast(
                forecast_id="HF-B",
                forecast_date=date(2026, 5, 3),
                target_trade_date=date(2026, 5, 4),
                forecast_group_key="rose::B",
                variety="rose",
                grade="B",
                predicted_harvest_qty=100,
            ),
        ]
        service = CapacityPlanningService()
        allocations = service.allocate_capacity(forecasts, PackingCapacityPlan(trade_date=date(2026, 5, 4)))

        self.assertEqual(sum(allocations.values()), 250)
        self.assertEqual(allocations["rose::A"], 167)
        self.assertEqual(allocations["rose::B"], 83)
        self.assertEqual(service.required_temp_workers(420, PackingCapacityPlan(trade_date=date(2026, 5, 4))), 2)

    def test_inventory_shortage_risk_levels(self) -> None:
        service = InventoryPlanningService()
        self.assertEqual(
            service.shortage_risk(sold_qty=100, predicted_harvest_qty=80, actual_stock_qty=20, field_buffer_qty=50),
            ShortageRisk.LOW,
        )
        self.assertEqual(
            service.shortage_risk(sold_qty=130, predicted_harvest_qty=80, actual_stock_qty=20, field_buffer_qty=50),
            ShortageRisk.MANAGEABLE,
        )
        self.assertEqual(
            service.shortage_risk(sold_qty=151, predicted_harvest_qty=80, actual_stock_qty=20, field_buffer_qty=50),
            ShortageRisk.HIGH,
        )

    def test_pricing_prefers_price_forecast_and_caps_discount(self) -> None:
        product = _product("SKU-001", last_price=Decimal("100"), recommended_price=Decimal("95"))
        forecast = PriceForecast(
            forecast_id="PF-001",
            forecast_date=date(2026, 5, 3),
            target_trade_date=date(2026, 5, 4),
            forecast_group_key="rose::A",
            variety="rose",
            grade="A",
            recommended_price=Decimal("70"),
        )
        window = TradeWindowService().build(date(2026, 5, 4), now=datetime(2026, 5, 4, 10, 0))

        decision = PricingDecisionService().decide(
            product=product,
            trade_date=date(2026, 5, 4),
            trade_window=window,
            price_forecast=forecast,
            break_even_price=Decimal("10"),
            absolute_min_price=Decimal("5"),
        )

        self.assertEqual(decision.pricing_source, PricingSource.FORECAST_PRICE)
        self.assertEqual(decision.target_price, Decimal("90.00"))
        self.assertFalse(decision.requires_manual_review)

    def test_clearance_below_break_even_requires_review(self) -> None:
        product = _product("SKU-001", last_price=Decimal("12"), recommended_price=None)
        forecast = PriceForecast(
            forecast_id="PF-001",
            forecast_date=date(2026, 5, 3),
            target_trade_date=date(2026, 5, 4),
            forecast_group_key="rose::A",
            variety="rose",
            grade="A",
            recommended_price=Decimal("8"),
        )
        window = TradeWindowService().build(date(2026, 5, 4), now=datetime(2026, 5, 4, 16, 0))

        decision = PricingDecisionService().decide(
            product=product,
            trade_date=date(2026, 5, 4),
            trade_window=window,
            price_forecast=forecast,
            break_even_price=Decimal("10"),
            absolute_min_price=Decimal("5"),
        )

        self.assertTrue(decision.requires_manual_review)
        self.assertEqual(decision.review_reason, "below_break_even_price")

    def test_predictive_task_generation_adds_capacity_review_and_forecast_price_tasks(self) -> None:
        products = [_product("SKU-A", grade="A"), _product("SKU-B", grade="B")]
        harvest_forecasts = [
            HarvestForecast(
                forecast_id="HF-A",
                forecast_date=date(2026, 5, 3),
                target_trade_date=date(2026, 5, 4),
                forecast_group_key="rose::A",
                variety="rose",
                grade="A",
                predicted_harvest_qty=200,
            ),
            HarvestForecast(
                forecast_id="HF-B",
                forecast_date=date(2026, 5, 3),
                target_trade_date=date(2026, 5, 4),
                forecast_group_key="rose::B",
                variety="rose",
                grade="B",
                predicted_harvest_qty=100,
            ),
        ]
        price_forecasts = [
            PriceForecast(
                forecast_id="PF-A",
                forecast_date=date(2026, 5, 3),
                target_trade_date=date(2026, 5, 4),
                forecast_group_key="rose::A",
                variety="rose",
                grade="A",
                recommended_price=Decimal("18"),
            ),
            PriceForecast(
                forecast_id="PF-B",
                forecast_date=date(2026, 5, 3),
                target_trade_date=date(2026, 5, 4),
                forecast_group_key="rose::B",
                variety="rose",
                grade="B",
                recommended_price=Decimal("16"),
            ),
        ]

        tasks = TaskGenerationService(PricingService(), ListingService()).generate(
            products,
            price_rules=[],
            listing_rules=[],
            harvest_forecasts=harvest_forecasts,
            price_forecasts=price_forecasts,
            capacity_plan=PackingCapacityPlan(trade_date=date(2026, 5, 4)),
            trade_date=date(2026, 5, 4),
            now=datetime(2026, 5, 3, 23, 30),
        )
        actions = {(task.internal_sku, task.action_type) for task in tasks}

        self.assertIn(("__operation__", TaskActionType.CAPACITY_WARNING), actions)
        self.assertIn(("__operation__", TaskActionType.LABOR_REQUIRED), actions)
        self.assertIn(("SKU-A", TaskActionType.SET_ONLINE), actions)
        self.assertIn(("SKU-A", TaskActionType.UPDATE_PRICE), actions)
        labor_task = next(task for task in tasks if task.action_type == TaskActionType.LABOR_REQUIRED)
        self.assertEqual(labor_task.required_by, datetime(2026, 5, 3, 20, 0))

    def test_closed_trade_window_only_generates_offline_not_online_or_price(self) -> None:
        product = _product("SKU-A", grade="A")
        forecast = HarvestForecast(
            forecast_id="HF-A",
            forecast_date=date(2026, 5, 3),
            target_trade_date=date(2026, 5, 4),
            forecast_group_key="rose::A",
            variety="rose",
            grade="A",
            predicted_harvest_qty=100,
        )
        tasks = TaskGenerationService(PricingService(), ListingService()).generate(
            [product],
            price_rules=[],
            listing_rules=[],
            harvest_forecasts=[forecast],
            capacity_plan=PackingCapacityPlan(trade_date=date(2026, 5, 4)),
            trade_date=date(2026, 5, 4),
            now=datetime(2026, 5, 4, 17, 1),
        )
        actions = {task.action_type for task in tasks}

        self.assertIn(TaskActionType.SET_OFFLINE, actions)
        self.assertNotIn(TaskActionType.SET_ONLINE, actions)
        self.assertNotIn(TaskActionType.UPDATE_PRICE, actions)

    def test_new_forecast_workbooks_load_with_english_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            harvest_path = root / "harvest_forecasts.xlsx"
            price_path = root / "price_forecasts.xlsx"

            harvest_workbook = Workbook()
            harvest_sheet = harvest_workbook.active
            harvest_sheet.append(HARVEST_FORECAST_HEADERS)
            harvest_sheet.append(["HF-001", "2026-05-03", "2026-05-04", "rose", "A", 120, 100, 140, "0.8", "manual", "", ""])
            harvest_workbook.save(harvest_path)

            price_workbook = Workbook()
            price_sheet = price_workbook.active
            price_sheet.append(PRICE_FORECAST_HEADERS)
            price_sheet.append(["PF-001", "2026-05-03", "2026-05-04", "rose", "A", 18, 16, 20, "0.8", "manual", "", ""])
            price_workbook.save(price_path)

            self.assertEqual(load_harvest_forecasts(harvest_path)[0].forecast_group_key, "rose::A")
            self.assertEqual(load_price_forecasts(price_path)[0].recommended_price, Decimal("18"))


if __name__ == "__main__":
    unittest.main()

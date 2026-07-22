from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal

from app.enums import ListingAction, ListingStrategy, PricingMethod, RoundingRule, TaskActionType
from app.exceptions import ValidationError
from app.models import ListingRule, PriceRule, Product
from app.services.ai import NullAISuggestionProvider
from app.services.listing import ListingService
from app.services.pricing import PricingService
from app.services.task_generation import TaskGenerationService


class ListingAndTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.products = [
            Product(
                internal_sku="SKU-ONLINE",
                product_name="online",
                grade="A",
                stem_length="60cm",
                unit="bundle",
                base_cost=Decimal("10"),
                current_stock=12,
                sale_enabled=True,
            ),
            Product(
                internal_sku="SKU-OFFLINE",
                product_name="offline",
                grade="B",
                stem_length="50cm",
                unit="bundle",
                base_cost=Decimal("8"),
                current_stock=0,
                sale_enabled=True,
            ),
            Product(
                internal_sku="SKU-DISABLED",
                product_name="disabled",
                grade="A",
                stem_length="70cm",
                unit="bundle",
                base_cost=Decimal("12"),
                current_stock=30,
                sale_enabled=False,
            ),
        ]
        self.price_rules = [
            PriceRule(
                rule_id="P1",
                rule_name="fixed",
                variety_filter="*",
                grade_filter="*",
                platform_filter="*",
                pricing_method=PricingMethod.FIXED_MARKUP,
                markup_value=Decimal("5"),
                min_price=None,
                rounding_rule=RoundingRule.ROUND,
                rounding_step=None,
                active=True,
                priority=10,
            )
        ]
        self.listing_rules = [
            ListingRule(
                rule_id="L1",
                rule_name="offline-on-zero",
                variety_filter="*",
                grade_filter="*",
                platform_filter="*",
                stock_threshold=Decimal("0"),
                listing_strategy=ListingStrategy.STOCK_BELOW_OFFLINE,
                active=True,
                priority=1,
            ),
            ListingRule(
                rule_id="L2",
                rule_name="online-on-restock",
                variety_filter="*",
                grade_filter="*",
                platform_filter="*",
                stock_threshold=Decimal("10"),
                listing_strategy=ListingStrategy.STOCK_ABOVE_ONLINE,
                active=True,
                priority=5,
            ),
        ]

    def test_listing_force_offline_when_sale_disabled(self) -> None:
        service = ListingService()
        action, _trace = service.evaluate(self.products[2], self.listing_rules)
        self.assertEqual(action, ListingAction.SET_OFFLINE.value)

    def test_task_generation_outputs_expected_actions(self) -> None:
        generator = TaskGenerationService(
            pricing_service=PricingService(ai_provider=NullAISuggestionProvider()),
            listing_service=ListingService(),
        )
        tasks = generator.generate(self.products, self.price_rules, self.listing_rules)
        actions = {(task.internal_sku, task.action_type) for task in tasks}
        self.assertIn(("SKU-ONLINE", TaskActionType.UPDATE_PRICE), actions)
        self.assertIn(("SKU-ONLINE", TaskActionType.SET_ONLINE), actions)
        self.assertIn(("SKU-OFFLINE", TaskActionType.SET_OFFLINE), actions)
        self.assertIn(("SKU-DISABLED", TaskActionType.SET_OFFLINE), actions)
        self.assertNotIn(("SKU-DISABLED", TaskActionType.UPDATE_PRICE), actions)

    def test_listing_platform_filter_can_force_online_for_matching_platform(self) -> None:
        service = ListingService(now_provider=lambda: datetime(2026, 4, 28, 22, 30, 0))
        platform_rule = ListingRule(
            rule_id="L3",
            rule_name="platform-online",
            variety_filter="online",
            grade_filter="A",
            platform_filter="蚂蚁",
            stock_threshold=Decimal("0"),
            listing_strategy=ListingStrategy.ALLOW_ONLINE,
            active=True,
            priority=0,
        )
        action, trace = service.evaluate(
            self.products[0],
            [platform_rule, *self.listing_rules],
            "蚂蚁花团供应商",
        )
        self.assertEqual(action, ListingAction.SET_ONLINE.value)
        self.assertTrue(any("L3" in item for item in trace))

    def test_listing_same_rank_conflict_is_not_silently_swallowed(self) -> None:
        rules = [
            ListingRule(
                rule_id="L-OFF",
                rule_name="offline",
                variety_filter="online",
                grade_filter="A",
                platform_filter="*",
                stock_threshold=Decimal("20"),
                listing_strategy=ListingStrategy.STOCK_BELOW_OFFLINE,
                active=True,
                priority=1,
            ),
            ListingRule(
                rule_id="L-ON",
                rule_name="online",
                variety_filter="online",
                grade_filter="A",
                platform_filter="*",
                stock_threshold=Decimal("10"),
                listing_strategy=ListingStrategy.STOCK_ABOVE_ONLINE,
                active=True,
                priority=1,
            ),
        ]

        with self.assertRaises(ValidationError) as context:
            ListingService().evaluate(self.products[0], rules)

        self.assertIn("上下架规则冲突", str(context.exception))


if __name__ == "__main__":
    unittest.main()

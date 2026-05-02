from __future__ import annotations

import unittest
from decimal import Decimal

from app.enums import ConditionType, ListingAction, PricingMethod, RoundingRule, TaskActionType
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
                variety="rose",
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
                variety="rose",
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
                variety="rose",
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
                scope_type="all",
                scope_value="*",
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
                condition_type=ConditionType.STOCK_LTE,
                condition_value=Decimal("0"),
                action=ListingAction.SET_OFFLINE,
                active=True,
                priority=1,
            ),
            ListingRule(
                rule_id="L2",
                rule_name="online-on-restock",
                condition_type=ConditionType.STOCK_GTE,
                condition_value=Decimal("10"),
                action=ListingAction.SET_ONLINE,
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


if __name__ == "__main__":
    unittest.main()

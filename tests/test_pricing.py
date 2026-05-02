from __future__ import annotations

import unittest
from decimal import Decimal

from app.enums import PricingMethod, PricingSource, RoundingRule
from app.models import PriceRule, Product
from app.services.ai import MockAISuggestionProvider, NullAISuggestionProvider
from app.services.pricing import PricingService


class PricingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.product = Product(
            internal_sku="SKU-001",
            product_name="rose",
            variety="rose",
            grade="A",
            stem_length="60cm",
            unit="bundle",
            base_cost=Decimal("10"),
            current_stock=15,
            sale_enabled=True,
            last_price=Decimal("18"),
        )
        self.rules = [
            PriceRule(
                rule_id="R1",
                rule_name="fixed",
                scope_type="all",
                scope_value="*",
                pricing_method=PricingMethod.FIXED_MARKUP,
                markup_value=Decimal("5"),
                min_price=Decimal("14"),
                rounding_rule=RoundingRule.ROUND,
                rounding_step=None,
                active=True,
                priority=10,
            ),
            PriceRule(
                rule_id="R2",
                rule_name="grade",
                scope_type="grade",
                scope_value="A",
                pricing_method=PricingMethod.PERCENTAGE_MARKUP,
                markup_value=Decimal("10"),
                min_price=None,
                rounding_rule=RoundingRule.STEP,
                rounding_step=Decimal("0.5"),
                active=True,
                priority=20,
            ),
        ]

    def test_rule_pricing_applies_markup_minimum_and_rounding(self) -> None:
        service = PricingService(ai_provider=NullAISuggestionProvider())
        decision = service.calculate(self.product, "default_platform", self.rules)
        self.assertEqual(decision.rule_price, Decimal("16.50"))
        self.assertEqual(decision.final_price, Decimal("16.50"))
        self.assertEqual(decision.pricing_source, PricingSource.RULE_ONLY)

    def test_mock_ai_keeps_rule_price_but_records_ai_trace(self) -> None:
        service = PricingService(ai_provider=MockAISuggestionProvider())
        decision = service.calculate(self.product, "default_platform", self.rules)
        self.assertEqual(decision.final_price, Decimal("16.50"))
        self.assertEqual(decision.pricing_source, PricingSource.RULE_PLUS_AI)
        self.assertIsNotNone(decision.ai_suggestion)
        self.assertEqual(decision.decision_trace["final_policy"], "rule_price_kept_for_current_stage")


if __name__ == "__main__":
    unittest.main()


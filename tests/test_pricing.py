from __future__ import annotations

import unittest
from decimal import Decimal

from app.enums import PricingMethod, PricingSource, RoundingRule
from app.exceptions import ValidationError
from app.models import PriceRule, Product
from app.services.ai import MockAISuggestionProvider, NullAISuggestionProvider
from app.services.pricing import PricingService, price_rule_matches, price_rule_specificity


def _rule(
    rule_id: str,
    *,
    variety_filter: str = "*",
    grade_filter: str = "*",
    platform_filter: str = "*",
    pricing_method: PricingMethod = PricingMethod.FIXED_MARKUP,
    markup_value: str = "5",
    min_price: str | None = None,
    rounding_rule: RoundingRule = RoundingRule.ROUND,
    rounding_step: str | None = None,
    priority: int = 10,
) -> PriceRule:
    return PriceRule(
        rule_id=rule_id,
        rule_name=rule_id,
        variety_filter=variety_filter,
        grade_filter=grade_filter,
        platform_filter=platform_filter,
        pricing_method=pricing_method,
        markup_value=Decimal(markup_value),
        min_price=Decimal(min_price) if min_price is not None else None,
        rounding_rule=rounding_rule,
        rounding_step=Decimal(rounding_step) if rounding_step is not None else None,
        active=True,
        priority=priority,
    )


class PricingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.product = Product(
            internal_sku="SKU-001",
            product_name="艾莎",
            grade="A",
            stem_length="60cm",
            unit="bundle",
            base_cost=Decimal("10"),
            current_stock=15,
            sale_enabled=True,
            last_price=Decimal("18"),
        )
        self.rules = [
            _rule("R1", min_price="14", priority=10),
            _rule(
                "R2",
                grade_filter="A",
                pricing_method=PricingMethod.PERCENTAGE_MARKUP,
                markup_value="10",
                rounding_rule=RoundingRule.STEP,
                rounding_step="0.5",
                priority=20,
            ),
        ]

    def test_rule_pricing_uses_single_winning_rule(self) -> None:
        service = PricingService(ai_provider=NullAISuggestionProvider())
        decision = service.calculate(self.product, "default_platform", self.rules)
        self.assertEqual(decision.rule_price, Decimal("15"))
        self.assertEqual(decision.final_price, Decimal("15"))
        self.assertEqual(decision.pricing_source, PricingSource.RULE_ONLY)
        self.assertEqual(decision.decision_trace["matched_rule_ids"], ["R1"])

    def test_mock_ai_keeps_rule_price_but_records_ai_trace(self) -> None:
        service = PricingService(ai_provider=MockAISuggestionProvider())
        decision = service.calculate(self.product, "default_platform", self.rules)
        self.assertEqual(decision.final_price, Decimal("15"))
        self.assertEqual(decision.pricing_source, PricingSource.RULE_PLUS_AI)
        self.assertIsNotNone(decision.ai_suggestion)
        self.assertEqual(decision.decision_trace["final_policy"], "rule_price_kept_for_current_stage")

    def test_three_dimensional_matching(self) -> None:
        all_rule = _rule("all")
        variety_rule = _rule("variety", variety_filter="艾莎")
        grade_rule = _rule("grade", variety_filter="艾莎", grade_filter="A")
        platform_rule = _rule("platform", variety_filter="艾莎", grade_filter="A", platform_filter="蚂蚁")
        platform_grade_rule = _rule("platform-grade", grade_filter="A", platform_filter="珍情")

        self.assertTrue(price_rule_matches(all_rule, self.product, "蚂蚁"))
        self.assertTrue(price_rule_matches(variety_rule, self.product, "蚂蚁"))
        self.assertTrue(price_rule_matches(grade_rule, self.product, "蚂蚁"))
        self.assertTrue(price_rule_matches(platform_rule, self.product, "蚂蚁"))
        self.assertTrue(price_rule_matches(platform_rule, self.product, "蚂蚁花团供应商"))
        self.assertFalse(price_rule_matches(platform_rule, self.product, "珍情"))
        self.assertTrue(price_rule_matches(platform_grade_rule, self.product, "珍情"))

    def test_priority_then_specificity_selects_winner(self) -> None:
        service = PricingService(ai_provider=NullAISuggestionProvider())
        rules = [
            _rule("all", priority=10, markup_value="1"),
            _rule("specific", variety_filter="艾莎", grade_filter="A", priority=10, markup_value="7"),
        ]
        decision = service.calculate(self.product, "蚂蚁", rules)
        self.assertEqual(decision.decision_trace["matched_rule_ids"], ["specific"])
        self.assertEqual(decision.rule_price, Decimal("17"))
        self.assertEqual(price_rule_specificity(rules[1]), 2)

    def test_conflicting_price_rules_are_not_randomly_swallowed(self) -> None:
        service = PricingService(ai_provider=NullAISuggestionProvider())
        rules = [
            _rule("R1", variety_filter="艾莎", priority=10, markup_value="1"),
            _rule("R2", grade_filter="A", priority=10, markup_value="2"),
        ]
        with self.assertRaises(ValidationError) as context:
            service.calculate(self.product, "蚂蚁", rules)
        self.assertIn("价格规则冲突", str(context.exception))

    def test_negative_markup_value_can_decrease_price(self) -> None:
        service = PricingService(ai_provider=NullAISuggestionProvider())
        fixed_decision = service.calculate(
            self.product,
            "蚂蚁",
            [_rule("fixed-decrease", markup_value="-2", priority=10, rounding_rule=RoundingRule.NONE)],
        )
        self.assertEqual(fixed_decision.rule_price, Decimal("8.00"))

        percent_decision = service.calculate(
            self.product,
            "蚂蚁",
            [
                _rule(
                    "percent-decrease",
                    pricing_method=PricingMethod.PERCENTAGE_MARKUP,
                    markup_value="-10",
                    priority=10,
                    rounding_rule=RoundingRule.NONE,
                )
            ],
        )
        self.assertEqual(percent_decision.rule_price, Decimal("9.00"))

    def test_relative_price_uses_current_platform_old_price(self) -> None:
        service = PricingService(ai_provider=NullAISuggestionProvider())
        decision = service.calculate(
            self.product,
            "蚂蚁",
            [_rule("relative", markup_value="5", rounding_rule=RoundingRule.NONE)],
            old_price=Decimal("23.40"),
            require_old_price=True,
        )
        self.assertEqual(decision.rule_price, Decimal("28.40"))
        self.assertEqual(decision.decision_trace["rule_steps"][0], "old_price=23.40")
        self.assertEqual(decision.expected_old_price, Decimal("23.40"))

    def test_relative_price_fails_closed_when_old_price_is_required(self) -> None:
        service = PricingService(ai_provider=NullAISuggestionProvider())
        with self.assertRaises(ValidationError) as context:
            service.calculate(
                self.product,
                "蚂蚁",
                [_rule("relative")],
                require_old_price=True,
            )
        self.assertIn("缺少上架状态当前价格", str(context.exception))


if __name__ == "__main__":
    unittest.main()

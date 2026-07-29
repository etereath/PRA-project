from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal

from app.enums import (
    ListingAction,
    ListingStrategy,
    PricingMethod,
    RoundingRule,
    TaskActionType,
    TaskOriginType,
)
from app.exceptions import ValidationError
from app.listing_identity import listing_identity_key
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
        tasks = generator.generate(
            self.products,
            self.price_rules,
            self.listing_rules,
            origin_ref_id="automation-run:rules-1",
        )
        actions = {(task.internal_sku, task.action_type) for task in tasks}
        self.assertIn(("SKU-ONLINE", TaskActionType.UPDATE_PRICE), actions)
        self.assertIn(("SKU-ONLINE", TaskActionType.SET_ONLINE), actions)
        self.assertIn(("SKU-OFFLINE", TaskActionType.SET_OFFLINE), actions)
        self.assertIn(("SKU-DISABLED", TaskActionType.SET_OFFLINE), actions)
        self.assertNotIn(("SKU-DISABLED", TaskActionType.UPDATE_PRICE), actions)
        self.assertTrue(
            all(
                task.origin_type is TaskOriginType.AUTOMATION
                and task.origin_ref_id == "automation-run:rules-1"
                for task in tasks
            )
        )

    def test_task_generation_filters_actions_by_current_platform_state(self) -> None:
        generator = TaskGenerationService(
            pricing_service=PricingService(ai_provider=NullAISuggestionProvider()),
            listing_service=ListingService(),
        )
        platform_name = "测试平台"
        online_identity = listing_identity_key(platform_name, "online", "A")
        offline_identity = listing_identity_key(platform_name, "offline", "B")
        disabled_identity = listing_identity_key(platform_name, "disabled", "A")
        ignored_candidates = []

        tasks = generator.generate(
            self.products,
            self.price_rules,
            self.listing_rules,
            platform_name=platform_name,
            old_prices={online_identity: Decimal("20")},
            platform_listing_states={
                online_identity: "online",
                offline_identity: "offline",
                disabled_identity: "online",
            },
            ignored_candidates=ignored_candidates,
        )

        actions = {(task.internal_sku, task.action_type) for task in tasks}
        self.assertIn(("SKU-ONLINE", TaskActionType.UPDATE_PRICE), actions)
        self.assertNotIn(("SKU-ONLINE", TaskActionType.SET_ONLINE), actions)
        self.assertNotIn(("SKU-OFFLINE", TaskActionType.SET_OFFLINE), actions)
        self.assertIn(("SKU-DISABLED", TaskActionType.SET_OFFLINE), actions)
        self.assertEqual(
            {
                (candidate.internal_sku, candidate.action_type, candidate.reason)
                for candidate in ignored_candidates
            },
            {
                (
                    "SKU-ONLINE",
                    TaskActionType.SET_ONLINE,
                    "当前商品已上架，无需重复上架",
                ),
                (
                    "SKU-OFFLINE",
                    TaskActionType.SET_OFFLINE,
                    "当前商品未上架，无需重复下架",
                ),
            },
        )

    def test_zero_stock_platform_snapshot_is_excluded_from_all_platform_actions(
        self,
    ) -> None:
        generator = TaskGenerationService(
            pricing_service=PricingService(ai_provider=NullAISuggestionProvider()),
            listing_service=ListingService(),
        )
        platform_name = "测试平台"
        identity = listing_identity_key(platform_name, "online", "A")

        ignored_candidates = []
        tasks = generator.generate(
            [self.products[0]],
            self.price_rules,
            self.listing_rules,
            platform_name=platform_name,
            old_prices={},
            platform_listing_states={identity: "unavailable"},
            ignored_candidates=ignored_candidates,
        )

        self.assertEqual(tasks, [])
        self.assertEqual(
            {
                (candidate.action_type, candidate.reason)
                for candidate in ignored_candidates
            },
            {
                (
                    TaskActionType.SET_ONLINE,
                    "平台库存为 0，商品不参与上架任务",
                ),
                (
                    TaskActionType.UPDATE_PRICE,
                    "平台库存为 0，商品不参与改价任务",
                ),
            },
        )

    def test_direct_set_offline_strategy_generates_set_offline_task(self) -> None:
        generator = TaskGenerationService(
            pricing_service=PricingService(ai_provider=NullAISuggestionProvider()),
            listing_service=ListingService(),
        )
        direct_offline_rule = ListingRule(
            rule_id="LIST-DIRECT-OFFLINE",
            rule_name="online A direct offline",
            variety_filter="online",
            grade_filter="A",
            platform_filter="测试平台",
            stock_threshold=Decimal("0"),
            listing_strategy=ListingStrategy.SET_OFFLINE,
            active=True,
            priority=1,
        )

        tasks = generator.generate(
            [self.products[0]],
            [],
            [direct_offline_rule],
            platform_name="测试平台",
            platform_listing_states={
                listing_identity_key("测试平台", "online", "A"): "online"
            },
        )

        offline_tasks = [
            task
            for task in tasks
            if task.action_type is TaskActionType.SET_OFFLINE
        ]
        self.assertEqual(len(offline_tasks), 1)
        self.assertEqual(
            offline_tasks[0].target_status,
            "offline",
        )

        ignored = generator.generate(
            [self.products[0]],
            [],
            [direct_offline_rule],
            platform_name="测试平台",
            platform_listing_states={
                listing_identity_key("测试平台", "online", "A"): "offline"
            },
        )
        self.assertEqual(ignored, [])

    def test_set_online_reads_latest_platform_price_and_inventory(self) -> None:
        generator = TaskGenerationService(
            pricing_service=PricingService(
                ai_provider=NullAISuggestionProvider()
            ),
            listing_service=ListingService(),
        )
        identity = listing_identity_key(
            "蚂蚁花团供应商",
            "online",
            "A",
        )

        tasks = generator.generate(
            self.products,
            self.price_rules,
            self.listing_rules,
            platform_name="蚂蚁花团供应商",
            platform_observations={
                identity: (Decimal("18.00"), 7),
            },
        )

        task = next(
            item
            for item in tasks
            if item.internal_sku == "SKU-ONLINE"
            and item.action_type is TaskActionType.SET_ONLINE
        )
        self.assertEqual(task.expected_old_price, Decimal("18.00"))
        self.assertEqual(task.target_price, Decimal("18.00"))
        self.assertEqual(task.target_inventory, 7)
        self.assertEqual(
            task.decision_trace["platform_observation"],
            {
                "observed_price": "18.00",
                "observed_inventory": 7,
            },
        )

    def test_new_product_without_snapshot_uses_base_cost_and_stock_for_set_online(self) -> None:
        generator = TaskGenerationService(
            pricing_service=PricingService(
                ai_provider=NullAISuggestionProvider()
            ),
            listing_service=ListingService(),
        )

        tasks = generator.generate(
            self.products,
            self.price_rules,
            self.listing_rules,
            platform_name="蚂蚁花团供应商",
            platform_observations={},
        )

        task = next(
            item
            for item in tasks
            if item.internal_sku == "SKU-ONLINE"
            and item.action_type is TaskActionType.SET_ONLINE
        )
        self.assertEqual(task.expected_old_price, Decimal("10"))
        self.assertEqual(task.target_price, Decimal("15.00"))
        self.assertEqual(task.target_inventory, 12)
        self.assertEqual(
            task.decision_trace["listing_target_default_source"],
            "product_base_cost_and_stock",
        )

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

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.enums import PricingMethod, RoundingRule, TaskActionType
from app.models import ListingStatus, PriceRule, Product, ShadowBotOperationLedger
from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.listing_status_policy import has_current_platform_stock, is_price_task_listing
from app.services.listing import ListingService
from app.services.pricing import PricingService
from app.services.shadowbot_executor import ShadowBotExecutor, ShadowBotResultContract
from app.services.task_generation import TaskGenerationService


class ListingStatusTests(unittest.TestCase):
    def test_zero_platform_stock_is_not_current_or_price_task_eligible(self) -> None:
        zero_stock = ListingStatus(
            listing_status_id="LISTING-ZERO",
            platform_name="蚂蚁花团供应商",
            internal_sku="",
            variety="艾莎",
            grade="A",
            current_price=Decimal("18.50"),
            platform_stock_qty=0,
            online_status="online",
        )

        self.assertFalse(has_current_platform_stock(zero_stock))
        self.assertFalse(is_price_task_listing(zero_stock))

    def test_price_tasks_only_include_products_with_online_listing_prices(self) -> None:
        products = [
            Product(
                internal_sku="SKU-ONLINE",
                product_name="艾莎",
                grade="A",
                stem_length="70",
                unit="扎",
                base_cost=Decimal("10"),
                current_stock=10,
                sale_enabled=True,
            ),
            Product(
                internal_sku="SKU-NOT-ONLINE",
                product_name="卡布奇诺",
                grade="A",
                stem_length="70",
                unit="扎",
                base_cost=Decimal("10"),
                current_stock=10,
                sale_enabled=True,
            ),
        ]
        price_rule = PriceRule(
            rule_id="PRICE-ALL",
            rule_name="全商品加价",
            variety_filter="*",
            grade_filter="*",
            platform_filter="*",
            pricing_method=PricingMethod.FIXED_MARKUP,
            markup_value=Decimal("2"),
            min_price=None,
            rounding_rule=RoundingRule.NONE,
            rounding_step=None,
            active=True,
            priority=1,
        )

        tasks = TaskGenerationService(PricingService(), ListingService()).generate(
            products,
            [price_rule],
            [],
            platform_name="蚂蚁",
            old_prices={("蚂蚁", "艾莎", "A"): Decimal("18.50")},
        )

        price_tasks = [task for task in tasks if task.action_type == TaskActionType.UPDATE_PRICE]
        self.assertEqual([task.internal_sku for task in price_tasks], ["SKU-ONLINE"])
        self.assertEqual(price_tasks[0].expected_old_price, Decimal("18.50"))
        self.assertEqual(price_tasks[0].target_price, Decimal("20.50"))

    def test_offline_task_does_not_require_old_price(self) -> None:
        product = Product(
            internal_sku="SKU-OFFLINE",
            product_name="艾莎",
            grade="A",
            stem_length="60",
            unit="扎",
            base_cost=Decimal("10"),
            current_stock=0,
            sale_enabled=False,
        )
        price_rule = PriceRule(
            rule_id="PRICE-1",
            rule_name="相对加价",
            variety_filter="*",
            grade_filter="*",
            platform_filter="*",
            pricing_method=PricingMethod.FIXED_MARKUP,
            markup_value=Decimal("2"),
            min_price=None,
            rounding_rule=RoundingRule.NONE,
            rounding_step=None,
            active=True,
            priority=1,
        )
        tasks = TaskGenerationService(PricingService(), ListingService()).generate(
            [product],
            [price_rule],
            [],
            platform_name="蚂蚁",
            old_prices={},
        )
        self.assertEqual([task.action_type for task in tasks], [TaskActionType.SET_OFFLINE])

    def test_new_listing_status_defaults_development_stock_to_100(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SQLiteRuntimeRepository(Path(temp_dir) / "runtime.sqlite3")
            repository.init_schema()
            repository.upsert_listing_status(
                ListingStatus(
                    listing_status_id="LISTING-1",
                    platform_name="蚂蚁",
                    internal_sku="SKU-001",
                    variety="艾莎",
                    current_price=Decimal("18.50"),
                    grade="C级",
                    sold_qty=3,
                )
            )
            status = repository.get_listing_status("蚂蚁", "艾莎", "C")
            self.assertIsNotNone(status)
            assert status is not None
            self.assertEqual(status.current_price, Decimal("18.50"))
            self.assertEqual(status.platform_stock_qty, 100)
            self.assertEqual(status.sold_qty, 3)

    def test_shadowbot_completed_result_updates_current_platform_price(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SQLiteRuntimeRepository(Path(temp_dir) / "runtime.sqlite3")
            repository.init_schema()
            observed_at = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
            repository.upsert_listing_status(
                ListingStatus(
                    listing_status_id="LISTING-1",
                    platform_name="蚂蚁",
                    internal_sku="SKU-001",
                    variety="艾莎",
                    current_price=Decimal("18.50"),
                    grade="C",
                    platform_stock_qty=37,
                    online_status="offline",
                    inventory_source="shadowbot",
                    inventory_observed_at=observed_at,
                    inventory_source_attempt_id="READ-1",
                )
            )
            executor = ShadowBotExecutor(repository, object())
            operation = ShadowBotOperationLedger(
                operation_id="OP-1",
                task_id="TASK-1",
                platform="蚂蚁",
                product_identity={"internal_sku": "DIFFERENT-SKU", "variety": "艾莎", "grade": "C级"},
                expected_old_price=Decimal("18.50"),
                target_price=Decimal("20.00"),
                status="RUNNING",
            )
            result = ShadowBotResultContract(
                execution_attempt_id="ATTEMPT-1",
                status="SUCCESS",
                run_success_flag=True,
                business_operation_completed=True,
                side_effect_state="VERIFIED",
                retryable=False,
                raw_output={"actual_price": "20.00"},
            )
            executor._update_listing_status_after_result(operation=operation, result=result)
            status = repository.get_listing_status("蚂蚁", "艾莎", "C")
            self.assertIsNotNone(status)
            assert status is not None
            self.assertEqual(status.current_price, Decimal("20.00"))
            self.assertEqual(status.platform_stock_qty, 37)
            self.assertEqual(status.online_status, "offline")
            self.assertEqual(status.inventory_source_attempt_id, "READ-1")
            self.assertEqual(status.source, "shadowbot")

    def test_shadowbot_inventory_observation_updates_default_and_rejects_stale_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SQLiteRuntimeRepository(Path(temp_dir) / "runtime.sqlite3")
            repository.init_schema()
            repository.upsert_listing_status(
                ListingStatus(
                    listing_status_id="LISTING-1",
                    platform_name="蚂蚁",
                    internal_sku="SKU-001",
                    variety="艾莎",
                    current_price=Decimal("18.50"),
                    grade="C",
                )
            )
            newer = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
            self.assertEqual(
                repository.apply_shadowbot_inventory_observation(
                    platform_name="蚂蚁",
                    variety="艾莎",
                    grade="C级",
                    observed_price=Decimal("19.25"),
                    platform_stock_qty=23,
                    online_status="offline",
                    observed_at=newer,
                    execution_attempt_id="READ-NEW",
                ),
                "UPDATED",
            )
            self.assertEqual(
                repository.apply_shadowbot_inventory_observation(
                    platform_name="蚂蚁",
                    variety="艾莎",
                    grade="C",
                    observed_price=Decimal("18.50"),
                    platform_stock_qty=99,
                    online_status="online",
                    observed_at=newer - timedelta(minutes=1),
                    execution_attempt_id="READ-OLD",
                ),
                "STALE_IGNORED",
            )
            status = repository.get_listing_status("蚂蚁", "艾莎", "C级")
            assert status is not None
            self.assertEqual(status.platform_stock_qty, 23)
            self.assertEqual(status.current_price, Decimal("19.25"))
            self.assertEqual(status.online_status, "offline")
            self.assertEqual(status.source, "shadowbot_read")
            self.assertEqual(status.inventory_source, "shadowbot")
            self.assertEqual(status.inventory_observed_at, newer)
            self.assertEqual(status.inventory_source_attempt_id, "READ-NEW")

    def test_listing_identity_does_not_depend_on_internal_sku(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SQLiteRuntimeRepository(Path(temp_dir) / "runtime.sqlite3")
            repository.init_schema()
            repository.upsert_listing_status(ListingStatus(
                listing_status_id="LISTING-1",
                platform_name="ANT_FLOWER_WECHAT",
                internal_sku="SKU-OLD",
                variety="艾莎",
                grade="C级",
                current_price=Decimal("18.50"),
            ))
            repository.upsert_listing_status(ListingStatus(
                listing_status_id="LISTING-2",
                platform_name="ant_flower_wechat",
                internal_sku="SKU-NEW",
                variety="艾莎",
                grade="C",
                current_price=Decimal("19.50"),
            ))

            rows = repository.list_listing_statuses(platform_name="ant_flower_wechat")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].internal_sku, "SKU-NEW")
            self.assertEqual(rows[0].current_price, Decimal("19.50"))


if __name__ == "__main__":
    unittest.main()

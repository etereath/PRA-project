from __future__ import annotations

from collections.abc import Iterable
from uuid import uuid4

from app.enums import PricingSource, TaskActionType, TaskStatus
from app.models import FinalPricingDecision, ListingRule, PriceRule, Product, Task
from app.services.listing import ListingService
from app.services.pricing import PricingService
from app.utils import utc_now


class TaskGenerationService:
    def __init__(self, pricing_service: PricingService, listing_service: ListingService) -> None:
        self.pricing_service = pricing_service
        self.listing_service = listing_service

    def generate(
        self,
        products: Iterable[Product],
        price_rules: list[PriceRule],
        listing_rules: list[ListingRule],
        platform_name: str = "default_platform",
    ) -> list[Task]:
        tasks: list[Task] = []
        dedupe: set[tuple[str, str, str]] = set()

        for product in products:
            pricing_decision = self.pricing_service.calculate(product, platform_name, price_rules)
            listing_action, listing_trace = self.listing_service.evaluate(product, listing_rules)

            if product.sale_enabled and product.current_stock > 0:
                update_key = (product.internal_sku, TaskActionType.UPDATE_PRICE.value, platform_name)
                if update_key not in dedupe:
                    dedupe.add(update_key)
                    tasks.append(self._price_task(pricing_decision))

            if listing_action:
                action_type = TaskActionType(listing_action)
                listing_key = (product.internal_sku, action_type.value, platform_name)
                if listing_key not in dedupe:
                    dedupe.add(listing_key)
                    tasks.append(
                        Task(
                            task_id=self._task_id(),
                            internal_sku=product.internal_sku,
                            platform_name=platform_name,
                            action_type=action_type,
                            priority=1 if action_type == TaskActionType.SET_OFFLINE else 5,
                            task_status=TaskStatus.PENDING,
                            created_at=utc_now(),
                            target_status=listing_action,
                            decision_trace={"listing_trace": listing_trace},
                        )
                    )

        tasks.sort(key=lambda item: (item.priority, item.internal_sku, item.action_type.value))
        return tasks

    def _price_task(self, pricing_decision: FinalPricingDecision) -> Task:
        return Task(
            task_id=self._task_id(),
            internal_sku=pricing_decision.internal_sku,
            platform_name=pricing_decision.platform_name,
            action_type=TaskActionType.UPDATE_PRICE,
            priority=10,
            task_status=TaskStatus.PENDING,
            created_at=utc_now(),
            target_price=pricing_decision.final_price,
            pricing_source=pricing_decision.pricing_source,
            decision_trace=pricing_decision.decision_trace,
        )

    def _task_id(self) -> str:
        return uuid4().hex[:12]


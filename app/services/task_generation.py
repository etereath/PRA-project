from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from uuid import uuid4

from app.enums import PricingSource, TaskActionType, TaskStatus, TradePhase
from app.models import (
    ColdStorageStatus,
    FinalPricingDecision,
    HarvestForecast,
    ListingRule,
    PackingCapacityPlan,
    PriceForecast,
    PriceRule,
    Product,
    ReviewRequirement,
    Task,
)
from app.services.capacity_planning import CapacityPlanningService
from app.services.harvest_forecast import HarvestForecastService, product_forecast_group_key
from app.services.inventory_planning import InventoryPlanningService
from app.services.listing_decision import ListingDecisionService
from app.services.listing import ListingService
from app.services.pricing import PricingService
from app.services.pricing_decision import PricingDecisionService
from app.services.trade_window import TradeWindowService
from app.utils import utc_now


class TaskGenerationService:
    def __init__(
        self,
        pricing_service: PricingService,
        listing_service: ListingService,
        *,
        inventory_planning_service: InventoryPlanningService | None = None,
    ) -> None:
        self.pricing_service = pricing_service
        self.listing_service = listing_service
        self.trade_window_service = TradeWindowService()
        self.harvest_forecast_service = HarvestForecastService()
        self.capacity_planning_service = CapacityPlanningService()
        self.inventory_planning_service = inventory_planning_service or InventoryPlanningService()
        self.listing_decision_service = ListingDecisionService()
        self.pricing_decision_service = PricingDecisionService()

    def generate(
        self,
        products: Iterable[Product],
        price_rules: list[PriceRule],
        listing_rules: list[ListingRule],
        platform_name: str = "default_platform",
        harvest_forecasts: list[HarvestForecast] | None = None,
        price_forecasts: list[PriceForecast] | None = None,
        capacity_plan: PackingCapacityPlan | None = None,
        cold_storage_status: ColdStorageStatus | None = None,
        trade_date: date | None = None,
        now: datetime | None = None,
        inventory_strategy: str = "conservative_v1",
    ) -> list[Task]:
        if harvest_forecasts or price_forecasts or capacity_plan or cold_storage_status or trade_date:
            return self._generate_predictive_tasks(
                products=list(products),
                platform_name=platform_name,
                harvest_forecasts=harvest_forecasts or [],
                price_forecasts=price_forecasts or [],
                capacity_plan=capacity_plan,
                cold_storage_status=cold_storage_status,
                trade_date=trade_date,
                now=now,
                inventory_strategy=inventory_strategy,
            )

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

    def _generate_predictive_tasks(
        self,
        *,
        products: list[Product],
        platform_name: str,
        harvest_forecasts: list[HarvestForecast],
        price_forecasts: list[PriceForecast],
        capacity_plan: PackingCapacityPlan | None,
        cold_storage_status: ColdStorageStatus | None,
        trade_date: date | None,
        now: datetime | None,
        inventory_strategy: str,
    ) -> list[Task]:
        self.inventory_planning_service = InventoryPlanningService(strategy_name=inventory_strategy)
        resolved_trade_date = self._resolve_trade_date(trade_date, harvest_forecasts, price_forecasts, capacity_plan)
        trade_window = self.trade_window_service.build(resolved_trade_date, now=now)
        capacity_plan = capacity_plan or PackingCapacityPlan(trade_date=resolved_trade_date)
        harvest_by_group = self.harvest_forecast_service.index_by_group(harvest_forecasts)
        price_by_group = {forecast.forecast_group_key: forecast for forecast in price_forecasts}
        allocations = self.capacity_planning_service.allocate_capacity(harvest_forecasts, capacity_plan)

        tasks: list[Task] = []
        dedupe: set[tuple[str, str, str]] = set()

        for review in self.capacity_planning_service.build_capacity_reviews(harvest_forecasts, capacity_plan):
            self._append_review_task(tasks, dedupe, review, platform_name)

        forecast_total_qty = self.capacity_planning_service.predicted_total_harvest_qty(harvest_forecasts)
        committable_total_qty = 0

        for product in products:
            group_key = product_forecast_group_key(product)
            harvest_forecast = harvest_by_group.get(group_key)
            price_forecast = price_by_group.get(group_key)
            inventory_plan = self.inventory_planning_service.build_inventory_plan(
                product=product,
                forecast=harvest_forecast,
                trade_date=resolved_trade_date,
                allocated_packing_capacity_qty=allocations.get(group_key, 0),
            )
            committable_total_qty += inventory_plan.committable_qty

            listing_decision = self.listing_decision_service.decide(
                product=product,
                inventory_plan=inventory_plan,
                trade_window=trade_window,
            )
            pricing_decision = self.pricing_decision_service.decide(
                product=product,
                trade_date=resolved_trade_date,
                trade_window=trade_window,
                price_forecast=price_forecast,
                break_even_price=product.base_cost,
                absolute_min_price=product.base_cost,
            )

            if listing_decision.shortage_risk.value == "high":
                self._append_task(
                    tasks,
                    dedupe,
                    Task(
                        task_id=self._task_id(),
                        internal_sku=product.internal_sku,
                        platform_name=platform_name,
                        action_type=TaskActionType.SHORTAGE_WARNING,
                        priority=2,
                        task_status=TaskStatus.PENDING,
                        created_at=utc_now(),
                        decision_trace=listing_decision.decision_trace | inventory_plan.decision_trace,
                        result_message=listing_decision.reason,
                    ),
                )

            if pricing_decision.requires_manual_review:
                review_type = (
                    TaskActionType.BELOW_BREAK_EVEN_REVIEW
                    if pricing_decision.review_reason == "below_break_even_price"
                    else TaskActionType.MANUAL_PRICE_REVIEW
                )
                self._append_task(
                    tasks,
                    dedupe,
                    Task(
                        task_id=self._task_id(),
                        internal_sku=product.internal_sku,
                        platform_name=platform_name,
                        action_type=review_type,
                        priority=3,
                        task_status=TaskStatus.MANUAL_REVIEW,
                        created_at=utc_now(),
                        target_price=pricing_decision.target_price,
                        pricing_source=pricing_decision.pricing_source,
                        decision_trace=pricing_decision.decision_trace,
                        result_message=pricing_decision.review_reason,
                        required_by=trade_window.trade_close_at
                        if review_type == TaskActionType.BELOW_BREAK_EVEN_REVIEW
                        else None,
                    ),
                )

            if listing_decision.should_offline:
                self._append_task(
                    tasks,
                    dedupe,
                    Task(
                        task_id=self._task_id(),
                        internal_sku=product.internal_sku,
                        platform_name=platform_name,
                        action_type=TaskActionType.SET_OFFLINE,
                        priority=1,
                        task_status=TaskStatus.PENDING,
                        created_at=utc_now(),
                        target_status=TaskActionType.SET_OFFLINE.value,
                        decision_trace=listing_decision.decision_trace,
                        result_message=listing_decision.reason,
                    ),
                )

            if listing_decision.should_online and trade_window.phase != TradePhase.CLOSED:
                self._append_task(
                    tasks,
                    dedupe,
                    Task(
                        task_id=self._task_id(),
                        internal_sku=product.internal_sku,
                        platform_name=platform_name,
                        action_type=TaskActionType.SET_ONLINE,
                        priority=5,
                        task_status=TaskStatus.PENDING,
                        created_at=utc_now(),
                        target_status=TaskActionType.SET_ONLINE.value,
                        decision_trace=listing_decision.decision_trace,
                    ),
                )

            if (
                listing_decision.should_online
                and not pricing_decision.requires_manual_review
                and pricing_decision.target_price is not None
                and trade_window.phase != TradePhase.CLOSED
            ):
                self._append_task(
                    tasks,
                    dedupe,
                    Task(
                        task_id=self._task_id(),
                        internal_sku=product.internal_sku,
                        platform_name=platform_name,
                        action_type=TaskActionType.UPDATE_PRICE,
                        priority=10,
                        task_status=TaskStatus.PENDING,
                        created_at=utc_now(),
                        target_price=pricing_decision.target_price,
                        pricing_source=pricing_decision.pricing_source,
                        decision_trace=pricing_decision.decision_trace,
                    ),
                )

        if cold_storage_status is not None:
            for review in self.inventory_planning_service.build_cold_storage_reviews(
                forecast_total_qty=forecast_total_qty,
                committable_total_qty=committable_total_qty,
                cold_storage_status=cold_storage_status,
            ):
                self._append_review_task(tasks, dedupe, review, platform_name)

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

    def _append_task(self, tasks: list[Task], dedupe: set[tuple[str, str, str]], task: Task) -> None:
        key = (task.internal_sku, task.action_type.value, task.platform_name)
        if key in dedupe:
            return
        dedupe.add(key)
        tasks.append(task)

    def _append_review_task(
        self,
        tasks: list[Task],
        dedupe: set[tuple[str, str, str]],
        review: ReviewRequirement,
        platform_name: str,
    ) -> None:
        self._append_task(
            tasks,
            dedupe,
            Task(
                task_id=self._task_id(),
                internal_sku=review.internal_sku,
                platform_name=platform_name,
                action_type=review.task_type,
                priority=2,
                task_status=TaskStatus.PENDING,
                created_at=utc_now(),
                decision_trace=review.details,
                result_message=review.reason,
                required_by=review.required_by,
            ),
        )

    def _resolve_trade_date(
        self,
        trade_date: date | None,
        harvest_forecasts: list[HarvestForecast],
        price_forecasts: list[PriceForecast],
        capacity_plan: PackingCapacityPlan | None,
    ) -> date:
        if trade_date is not None:
            return trade_date
        if capacity_plan is not None:
            return capacity_plan.trade_date
        if harvest_forecasts:
            return harvest_forecasts[0].target_trade_date
        if price_forecasts:
            return price_forecasts[0].target_trade_date
        return (datetime.now() + timedelta(days=1)).date()

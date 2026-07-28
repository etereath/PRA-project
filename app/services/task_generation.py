from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.enums import TaskActionType, TaskStatus, TradePhase
from app.listing_identity import listing_identity_key
from app.models import (
    ColdStorageStatus,
    FinalPricingDecision,
    HarvestForecast,
    IgnoredTaskCandidate,
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
from app.services.pricing import PricingService, price_rule_matches
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
        old_prices: dict[tuple[str, str, str], Decimal] | None = None,
        platform_observations: dict[
            tuple[str, str, str],
            tuple[Decimal, int],
        ]
        | None = None,
        platform_listing_states: dict[tuple[str, str, str], str] | None = None,
        ignored_candidates: list[IgnoredTaskCandidate] | None = None,
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
                platform_observations=platform_observations,
                platform_listing_states=platform_listing_states,
                ignored_candidates=ignored_candidates,
            )

        tasks: list[Task] = []
        dedupe: set[tuple[str, str, str]] = set()

        for product in products:
            listing_action, listing_trace = self.listing_service.evaluate(product, listing_rules, platform_name)
            pricing_decision: FinalPricingDecision | None = None
            identity = listing_identity_key(
                platform_name,
                product.product_name,
                product.grade,
            )
            observed_listing = (
                platform_observations.get(identity)
                if platform_observations is not None
                else None
            )
            current_listing_state = (
                platform_listing_states.get(identity)
                if platform_listing_states is not None
                else None
            )

            if product.sale_enabled and product.current_stock > 0:
                matched_price_rule_ids = [
                    rule.rule_id
                    for rule in price_rules
                    if (
                        rule.active
                        and price_rule_matches(rule, product, platform_name)
                    )
                ]
                price_rule_applies = bool(matched_price_rule_ids)
                participates_in_price_generation = (
                    (
                        platform_listing_states is None
                        or current_listing_state == "online"
                    )
                    and (old_prices is None or identity in old_prices)
                )
                if (
                    price_rule_applies
                    and not participates_in_price_generation
                    and ignored_candidates is not None
                ):
                    self._append_ignored_candidate(
                        ignored_candidates,
                        product=product,
                        platform_name=platform_name,
                        action_type=TaskActionType.UPDATE_PRICE,
                        current_listing_state=current_listing_state,
                        matched_rule_ids=matched_price_rule_ids,
                    )
                if participates_in_price_generation:
                    old_price = old_prices.get(identity) if old_prices is not None else None
                    pricing_decision = self.pricing_service.calculate(
                        product,
                        platform_name,
                        price_rules,
                        old_price=old_price,
                        require_old_price=old_prices is not None,
                    )
                    update_key = (product.internal_sku, TaskActionType.UPDATE_PRICE.value, platform_name)
                    if update_key not in dedupe:
                        dedupe.add(update_key)
                        tasks.append(self._price_task(pricing_decision))

            if listing_action:
                action_type = TaskActionType(listing_action)
                if not self._listing_state_allows_action(
                    action_type,
                    current_listing_state,
                    platform_listing_states is not None,
                ):
                    if ignored_candidates is not None:
                        self._append_ignored_candidate(
                            ignored_candidates,
                            product=product,
                            platform_name=platform_name,
                            action_type=action_type,
                            current_listing_state=current_listing_state,
                            matched_rule_ids=[
                                str(step).split(":", 2)[1]
                                for step in listing_trace
                                if str(step).startswith("matched:")
                            ],
                        )
                    continue
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
                            target_price=(
                                observed_listing[0]
                                if action_type is TaskActionType.SET_ONLINE
                                and observed_listing is not None
                                else pricing_decision.final_price
                                if action_type is TaskActionType.SET_ONLINE
                                and pricing_decision is not None
                                else product.base_cost
                                if action_type is TaskActionType.SET_ONLINE
                                else None
                            ),
                            expected_old_price=(
                                observed_listing[0]
                                if action_type is TaskActionType.SET_ONLINE
                                and observed_listing is not None
                                else product.base_cost
                                if action_type is TaskActionType.SET_ONLINE
                                else None
                            ),
                            target_inventory=(
                                observed_listing[1]
                                if action_type is TaskActionType.SET_ONLINE
                                and observed_listing is not None
                                else product.current_stock
                                if action_type is TaskActionType.SET_ONLINE
                                else None
                            ),
                            target_status=(
                                "online"
                                if action_type is TaskActionType.SET_ONLINE
                                else "offline"
                            ),
                            decision_trace={
                                "listing_trace": listing_trace,
                                "platform_observation": (
                                    {
                                        "observed_price": str(
                                            observed_listing[0]
                                        ),
                                        "observed_inventory": observed_listing[1],
                                    }
                                    if observed_listing is not None
                                    else None
                                ),
                                "listing_target_default_source": (
                                    "platform_snapshot"
                                    if observed_listing is not None
                                    else "product_base_cost_and_stock"
                                ),
                            },
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
        platform_observations: dict[
            tuple[str, str, str],
            tuple[Decimal, int],
        ]
        | None,
        platform_listing_states: dict[tuple[str, str, str], str] | None,
        ignored_candidates: list[IgnoredTaskCandidate] | None,
    ) -> list[Task]:
        self.inventory_planning_service = InventoryPlanningService(strategy_name=inventory_strategy)
        resolved_trade_date = self._resolve_trade_date(trade_date, harvest_forecasts, price_forecasts, capacity_plan)
        trade_window = self.trade_window_service.build(resolved_trade_date, now=now)
        capacity_plan = capacity_plan or PackingCapacityPlan(trade_date=resolved_trade_date)
        if capacity_plan.trade_date != resolved_trade_date:
            capacity_plan = replace(capacity_plan, trade_date=resolved_trade_date)
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
            identity = listing_identity_key(
                platform_name,
                product.product_name,
                product.grade,
            )
            observed_listing = (
                platform_observations.get(identity)
                if platform_observations is not None
                else None
            )
            current_listing_state = (
                platform_listing_states.get(identity)
                if platform_listing_states is not None
                else None
            )
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

            if listing_decision.should_offline and not self._listing_state_allows_action(
                TaskActionType.SET_OFFLINE,
                current_listing_state,
                platform_listing_states is not None,
            ):
                if ignored_candidates is not None:
                    self._append_ignored_candidate(
                        ignored_candidates,
                        product=product,
                        platform_name=platform_name,
                        action_type=TaskActionType.SET_OFFLINE,
                        current_listing_state=current_listing_state,
                    )

            if (
                listing_decision.should_offline
                and self._listing_state_allows_action(
                    TaskActionType.SET_OFFLINE,
                    current_listing_state,
                    platform_listing_states is not None,
                )
            ):
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
                        target_status="offline",
                        decision_trace=listing_decision.decision_trace,
                        result_message=listing_decision.reason,
                    ),
                )

            should_generate_online_action = (
                listing_decision.should_online
                and not pricing_decision.requires_manual_review
                and pricing_decision.target_price is not None
                and trade_window.phase != TradePhase.CLOSED
            )
            if should_generate_online_action and not self._listing_state_allows_action(
                TaskActionType.SET_ONLINE,
                current_listing_state,
                platform_listing_states is not None,
            ):
                if ignored_candidates is not None:
                    self._append_ignored_candidate(
                        ignored_candidates,
                        product=product,
                        platform_name=platform_name,
                        action_type=TaskActionType.SET_ONLINE,
                        current_listing_state=current_listing_state,
                    )

            if (
                should_generate_online_action
                and self._listing_state_allows_action(
                    TaskActionType.SET_ONLINE,
                    current_listing_state,
                    platform_listing_states is not None,
                )
            ):
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
                        expected_old_price=(
                            observed_listing[0]
                            if observed_listing is not None
                            else product.base_cost
                        ),
                        target_price=(
                            observed_listing[0]
                            if observed_listing is not None
                            else pricing_decision.target_price
                        ),
                        target_inventory=(
                            observed_listing[1]
                            if observed_listing is not None
                            else inventory_plan.committable_qty
                        ),
                        target_status="online",
                        decision_trace=listing_decision.decision_trace
                        | {
                            "platform_observation": (
                                {
                                    "observed_price": str(
                                        observed_listing[0]
                                    ),
                                    "observed_inventory": observed_listing[1],
                                }
                                if observed_listing is not None
                                else None
                            ),
                            "listing_target_default_source": (
                                "platform_snapshot"
                                if observed_listing is not None
                                else "product_base_cost_and_planned_stock"
                            ),
                        },
                    ),
                )

            if (
                should_generate_online_action
                and (
                    platform_listing_states is None
                    or current_listing_state == "online"
                )
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
            elif (
                should_generate_online_action
                and platform_listing_states is not None
                and ignored_candidates is not None
            ):
                self._append_ignored_candidate(
                    ignored_candidates,
                    product=product,
                    platform_name=platform_name,
                    action_type=TaskActionType.UPDATE_PRICE,
                    current_listing_state=current_listing_state,
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
            expected_old_price=pricing_decision.expected_old_price,
            target_price=pricing_decision.final_price,
            pricing_source=pricing_decision.pricing_source,
            decision_trace=pricing_decision.decision_trace,
        )

    @staticmethod
    def _listing_state_allows_action(
        action_type: TaskActionType,
        current_listing_state: str | None,
        has_platform_state_snapshot: bool,
    ) -> bool:
        if not has_platform_state_snapshot:
            return True
        if action_type is TaskActionType.SET_ONLINE:
            return current_listing_state in {None, "offline"}
        if action_type is TaskActionType.SET_OFFLINE:
            return current_listing_state == "online"
        return True

    @staticmethod
    def _append_ignored_candidate(
        ignored_candidates: list[IgnoredTaskCandidate],
        *,
        product: Product,
        platform_name: str,
        action_type: TaskActionType,
        current_listing_state: str | None,
        matched_rule_ids: list[str] | None = None,
    ) -> None:
        state = current_listing_state or "missing"
        reasons = {
            (TaskActionType.SET_ONLINE, "online"): "当前商品已上架，无需重复上架",
            (TaskActionType.SET_ONLINE, "unavailable"): "平台库存为 0，商品不参与上架任务",
            (TaskActionType.SET_ONLINE, "unknown"): "当前平台上架状态未知，上架任务已忽略",
            (TaskActionType.SET_OFFLINE, "offline"): "当前商品未上架，无需重复下架",
            (TaskActionType.SET_OFFLINE, "missing"): "未找到当前平台上架状态，下架任务已忽略",
            (TaskActionType.SET_OFFLINE, "unavailable"): "平台库存为 0，商品不参与下架任务",
            (TaskActionType.SET_OFFLINE, "unknown"): "当前平台上架状态未知，下架任务已忽略",
            (TaskActionType.UPDATE_PRICE, "offline"): "当前商品未上架，改价任务已忽略",
            (TaskActionType.UPDATE_PRICE, "missing"): "未找到当前平台上架状态，改价任务已忽略",
            (TaskActionType.UPDATE_PRICE, "unavailable"): "平台库存为 0，商品不参与改价任务",
            (TaskActionType.UPDATE_PRICE, "unknown"): "当前平台上架状态未知，改价任务已忽略",
        }
        candidate = IgnoredTaskCandidate(
            internal_sku=product.internal_sku,
            product_name=product.product_name,
            grade=product.grade,
            platform_name=platform_name,
            action_type=action_type,
            current_listing_state=state,
            reason=reasons.get(
                (action_type, state),
                "当前平台状态不符合任务生成条件，任务已忽略",
            ),
            matched_rule_ids=list(matched_rule_ids or []),
        )
        key = (
            candidate.internal_sku,
            candidate.platform_name,
            candidate.action_type,
        )
        if any(
            (
                existing.internal_sku,
                existing.platform_name,
                existing.action_type,
            )
            == key
            for existing in ignored_candidates
        ):
            return
        ignored_candidates.append(candidate)

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

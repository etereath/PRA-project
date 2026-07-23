from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time

from app.enums import ListingAction, ListingStrategy
from app.exceptions import ValidationError
from app.models import ListingRule, Product
from app.platform_identity import platform_names_match


class ListingService:
    def __init__(self, now_provider: Callable[[], datetime] | None = None) -> None:
        self.now_provider = now_provider or datetime.now

    def evaluate(
        self,
        product: Product,
        rules: list[ListingRule],
        platform_name: str = "default_platform",
    ) -> tuple[str | None, list[str]]:
        active_rules = sorted((rule for rule in rules if rule.active), key=lambda item: item.priority)
        traces: list[str] = []
        selected_action: str | None = None

        if not product.sale_enabled:
            traces.append("sale_enabled=false->force_set_offline")
            return ListingAction.SET_OFFLINE.value, traces

        actionable_matches = [
            (rule, action)
            for rule in active_rules
            if (action := self._evaluate_rule(product, rule, platform_name)) is not None
        ]
        self._raise_on_same_rank_conflicts(actionable_matches)

        for rule, action in actionable_matches:
            traces.append(f"matched:{rule.rule_id}:{rule.listing_strategy.value}->{action.value}")
            if action == ListingAction.SET_OFFLINE:
                return ListingAction.SET_OFFLINE.value, traces
            if action == ListingAction.SET_ONLINE:
                selected_action = ListingAction.SET_ONLINE.value
        return selected_action, traces

    def _evaluate_rule(self, product: Product, rule: ListingRule, platform_name: str) -> ListingAction | None:
        if not self._matches_filter(rule.variety_filter, product.product_name):
            return None
        if not self._matches_grade_filter(rule.grade_filter, product.grade):
            return None
        if not self._matches_platform_filter(rule.platform_filter, platform_name):
            return None
        threshold = int(rule.stock_threshold)
        if rule.listing_strategy == ListingStrategy.PROHIBIT_ONLINE:
            return ListingAction.SET_OFFLINE
        if rule.listing_strategy == ListingStrategy.ALLOW_ONLINE:
            return ListingAction.SET_ONLINE
        if rule.listing_strategy == ListingStrategy.STOCK_BELOW_OFFLINE and product.current_stock <= threshold:
            return ListingAction.SET_OFFLINE
        if rule.listing_strategy == ListingStrategy.STOCK_ABOVE_ONLINE and product.current_stock >= threshold:
            return ListingAction.SET_ONLINE
        return None

    def _matches_filter(self, filter_value: str, actual_value: str) -> bool:
        normalized_filter = str(filter_value or "*").strip()
        if normalized_filter == "*":
            return True
        return normalized_filter == str(actual_value or "").strip()

    def _matches_grade_filter(self, filter_value: str, actual_value: str) -> bool:
        normalized_filter = str(filter_value or "*").strip().upper()
        if normalized_filter == "*":
            return True
        return normalized_filter == str(actual_value or "").strip().upper()

    def _matches_platform_filter(self, filter_value: str, actual_value: str) -> bool:
        normalized_filter = str(filter_value or "*").strip()
        return normalized_filter == "*" or platform_names_match(normalized_filter, actual_value)

    def _raise_on_same_rank_conflicts(self, matches: list[tuple[ListingRule, ListingAction]]) -> None:
        by_rank: dict[tuple[int, int], list[tuple[ListingRule, ListingAction]]] = {}
        for rule, action in matches:
            by_rank.setdefault((rule.priority, _listing_rule_specificity(rule)), []).append((rule, action))
        for (priority, specificity), items in by_rank.items():
            actions = {action.value for _rule, action in items}
            if len(actions) <= 1:
                continue
            rule_ids = ", ".join(rule.rule_id for rule, _action in items)
            raise ValidationError(
                "上下架规则冲突："
                f"{rule_ids} 的优先级 {priority} 和具体度 {specificity} 相同，但动作不同，请调整后重新生成任务。"
            )


def _listing_rule_specificity(rule: ListingRule) -> int:
    return sum(1 for value in (rule.variety_filter, rule.grade_filter, rule.platform_filter) if str(value or "*").strip() != "*")

    def _matches(self, product: Product, rule: ListingRule, now_time: time) -> bool:
        action = self._evaluate_rule(product, rule, "default_platform")
        return action is not None

    def _parse_time(self, raw_value: object) -> time | None:
        if raw_value in (None, ""):
            return None
        value = str(raw_value).strip()
        if len(value) == 5 and value.count(":") == 1:
            hour_str, minute_str = value.split(":")
            try:
                hour = int(hour_str)
                minute = int(minute_str)
                return time(hour=hour, minute=minute)
            except ValueError:
                return None
        return None

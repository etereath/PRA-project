from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time

from app.enums import ConditionType, ListingAction
from app.models import ListingRule, Product


class ListingService:
    def __init__(self, now_provider: Callable[[], datetime] | None = None) -> None:
        self.now_provider = now_provider or datetime.now

    def evaluate(self, product: Product, rules: list[ListingRule]) -> tuple[str | None, list[str]]:
        active_rules = sorted((rule for rule in rules if rule.active), key=lambda item: item.priority)
        traces: list[str] = []
        selected_action: str | None = None
        now_time = self.now_provider().time()

        if not product.sale_enabled:
            traces.append("sale_enabled=false->force_set_offline")
            return ListingAction.SET_OFFLINE.value, traces

        for rule in active_rules:
            if rule.condition_type == ConditionType.TIME_GTE and self._matches(product, rule, now_time):
                traces.append(f"matched:{rule.rule_id}:{rule.action.value}")
                if rule.action == ListingAction.SET_ONLINE:
                    return ListingAction.SET_ONLINE.value, traces

        for rule in active_rules:
            if self._matches(product, rule, now_time):
                traces.append(f"matched:{rule.rule_id}:{rule.action.value}")
                if rule.action == ListingAction.SET_OFFLINE:
                    return ListingAction.SET_OFFLINE.value, traces
                selected_action = ListingAction.SET_ONLINE.value
        return selected_action, traces

    def _matches(self, product: Product, rule: ListingRule, now_time: time) -> bool:
        if rule.condition_type == ConditionType.SALE_DISABLED:
            return not product.sale_enabled
        if rule.condition_type == ConditionType.STOCK_LTE:
            return product.current_stock <= int(rule.condition_value or 0)  # type: ignore[arg-type]
        if rule.condition_type == ConditionType.STOCK_GTE:
            return product.current_stock >= int(rule.condition_value or 0)  # type: ignore[arg-type]
        if rule.condition_type == ConditionType.TIME_GTE:
            threshold = self._parse_time(rule.condition_value)
            if threshold is None:
                return False
            return now_time >= threshold
        return False

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

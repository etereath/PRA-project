from __future__ import annotations

from app.enums import ConditionType, ListingAction
from app.models import ListingRule, Product


class ListingService:
    def evaluate(self, product: Product, rules: list[ListingRule]) -> tuple[str | None, list[str]]:
        active_rules = sorted((rule for rule in rules if rule.active), key=lambda item: item.priority)
        traces: list[str] = []
        selected_action: str | None = None

        if not product.sale_enabled:
            traces.append("sale_enabled=false->force_set_offline")
            return ListingAction.SET_OFFLINE.value, traces

        for rule in active_rules:
            if self._matches(product, rule):
                traces.append(f"matched:{rule.rule_id}:{rule.action.value}")
                if rule.action == ListingAction.SET_OFFLINE:
                    return ListingAction.SET_OFFLINE.value, traces
                selected_action = ListingAction.SET_ONLINE.value
        return selected_action, traces

    def _matches(self, product: Product, rule: ListingRule) -> bool:
        if rule.condition_type == ConditionType.SALE_DISABLED:
            return not product.sale_enabled
        if rule.condition_type == ConditionType.STOCK_LTE:
            return product.current_stock <= int(rule.condition_value or 0)
        if rule.condition_type == ConditionType.STOCK_GTE:
            return product.current_stock >= int(rule.condition_value or 0)
        return False


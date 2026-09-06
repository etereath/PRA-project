from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

from app.enums import PricingMethod, PricingSource, RoundingRule
from app.exceptions import ValidationError
from app.models import (
    AISuggestionInput,
    FinalPricingDecision,
    PriceRule,
    Product,
    RulePricingInput,
    RulePricingResult,
)
from app.platform_identity import platform_names_match
from app.services.ai import AISuggestionProvider, NullAISuggestionProvider


class PricingService:
    def __init__(self, ai_provider: AISuggestionProvider | None = None) -> None:
        self.ai_provider = ai_provider or NullAISuggestionProvider()

    def calculate(
        self,
        product: Product,
        platform_name: str,
        rules: list[PriceRule],
        *,
        old_price: Decimal | None = None,
        require_old_price: bool = False,
    ) -> FinalPricingDecision:
        rule_result = self._calculate_rule_price(
            RulePricingInput(
                product=product,
                platform_name=platform_name,
                rules=rules,
                old_price=old_price if old_price is not None else (None if require_old_price else product.base_cost),
            )
        )
        ai_input = AISuggestionInput(
            internal_sku=product.internal_sku,
            product_name=product.product_name,
            platform_name=platform_name,
            cost=product.base_cost,
            stock=product.current_stock,
            last_price=product.last_price,
            features=product.metadata
            | {
                "grade": product.grade,
                "recommended_price": str(product.recommended_price) if product.recommended_price is not None else None,
            },
        )
        ai_result = self.ai_provider.suggest(ai_input)
        final_price = rule_result.rule_price
        source = PricingSource.RULE_ONLY
        if ai_result and ai_result.suggested_price is not None:
            source = PricingSource.RULE_PLUS_AI
        decision_trace = {
            "matched_rule_ids": rule_result.matched_rule_ids,
            "matched_rule_names": rule_result.matched_rule_names,
            "rule_steps": rule_result.applied_steps,
            "rule_price": str(rule_result.rule_price),
            "ai_considered": ai_result is not None,
            "ai_suggested_price": str(ai_result.suggested_price) if ai_result and ai_result.suggested_price is not None else None,
            "ai_confidence": str(ai_result.confidence) if ai_result and ai_result.confidence is not None else None,
            "ai_reason": ai_result.reason if ai_result else "",
            "final_policy": "rule_price_kept_for_current_stage",
        }
        return FinalPricingDecision(
            internal_sku=product.internal_sku,
            platform_name=platform_name,
            expected_old_price=rule_result.old_price,
            rule_price=rule_result.rule_price,
            final_price=final_price,
            pricing_source=source,
            decision_trace=decision_trace,
            ai_suggestion=ai_result,
        )

    def _calculate_rule_price(self, request: RulePricingInput) -> RulePricingResult:
        applicable = [
            rule
            for rule in request.rules
            if rule.active and price_rule_matches(rule, request.product, request.platform_name)
        ]
        selected_rule = self._select_winning_rule(applicable)
        matched_rule_ids: list[str] = []
        matched_rule_names: list[str] = []
        steps: list[str] = []
        price = request.old_price
        if selected_rule is not None:
            if price is None:
                raise ValidationError(
                    f"{request.platform_name} / {request.product.internal_sku} 缺少上架状态当前价格，"
                    "无法生成相对改价任务。请先运行 ShadowBot READ_ONLY 同步平台价格。"
                )
            rule = selected_rule
            steps.append(f"old_price={price}")
            matched_rule_ids.append(rule.rule_id)
            matched_rule_names.append(rule.rule_name)
            steps.append(
                "winning_rule:"
                f"{rule.rule_id}:priority={rule.priority}:specificity={price_rule_specificity(rule)}"
            )
            price = self._apply_method(price, rule)
            steps.append(f"rule:{rule.rule_id}:{rule.pricing_method.value}->{price}")
            if rule.min_price is not None and price < rule.min_price:
                price = rule.min_price
                steps.append(f"rule:{rule.rule_id}:min_price->{price}")
            price = self._apply_rounding(price, rule)
            steps.append(f"rule:{rule.rule_id}:rounded->{price}")
        if price is None:
            price = request.product.base_cost
            steps.append(f"no_matching_rule:base_cost={price}")
        return RulePricingResult(
            matched_rule_ids=matched_rule_ids,
            matched_rule_names=matched_rule_names,
            old_price=request.old_price,
            rule_price=price.quantize(Decimal("0.01")),
            applied_steps=steps,
        )

    def _select_winning_rule(self, rules: list[PriceRule]) -> PriceRule | None:
        if not rules:
            return None
        sorted_rules = sorted(rules, key=lambda item: (item.priority, -price_rule_specificity(item), item.rule_id))
        winner = sorted_rules[0]
        conflicts = [
            rule
            for rule in sorted_rules[1:]
            if rule.priority == winner.priority and price_rule_specificity(rule) == price_rule_specificity(winner)
        ]
        if conflicts:
            conflict_ids = ", ".join([winner.rule_id, *(rule.rule_id for rule in conflicts)])
            raise ValidationError(f"价格规则冲突：{conflict_ids} 的优先级和具体度相同，请调整后重新生成任务。")
        return winner

    def _apply_method(self, price: Decimal, rule: PriceRule) -> Decimal:
        if rule.pricing_method == PricingMethod.FIXED_MARKUP:
            return price + rule.markup_value
        if rule.pricing_method == PricingMethod.PERCENTAGE_MARKUP:
            return price * (Decimal("1") + rule.markup_value / Decimal("100"))
        return price

    def _apply_rounding(self, price: Decimal, rule: PriceRule) -> Decimal:
        if rule.rounding_rule == RoundingRule.NONE:
            return price
        if rule.rounding_rule == RoundingRule.ROUND:
            return price.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if rule.rounding_rule == RoundingRule.CEIL:
            return price.quantize(Decimal("1"), rounding=ROUND_CEILING)
        if rule.rounding_rule == RoundingRule.FLOOR:
            return price.quantize(Decimal("1"), rounding=ROUND_FLOOR)
        if rule.rounding_rule == RoundingRule.STEP:
            step = rule.rounding_step or Decimal("1")
            quotient = (price / step).to_integral_value(rounding=ROUND_CEILING)
            return quotient * step
        return price


def price_rule_matches(rule: PriceRule, product: Product, platform_name: str) -> bool:
    return (
        _matches_filter(rule.variety_filter, product.product_name, kind="text")
        and _matches_filter(rule.grade_filter, product.grade, kind="grade")
        and _matches_platform_filter(rule.platform_filter, platform_name)
    )


def price_rule_specificity(rule: PriceRule) -> int:
    return sum(
        1
        for value in (rule.variety_filter, rule.grade_filter, rule.platform_filter)
        if _normalize_filter_value(value, kind="text") != "*"
    )


def _matches_filter(filter_value: object, actual_value: object, *, kind: str) -> bool:
    normalized_filter = _normalize_filter_value(filter_value, kind=kind)
    if normalized_filter == "*":
        return True
    return normalized_filter == _normalize_filter_value(actual_value, kind=kind)


def _matches_platform_filter(filter_value: object, actual_value: object) -> bool:
    normalized_filter = _normalize_filter_value(filter_value, kind="text")
    return normalized_filter == "*" or platform_names_match(filter_value, actual_value)


def _normalize_filter_value(value: object, *, kind: str) -> str:
    text = str(value if value is not None else "").strip()
    if text == "*":
        return "*"
    if kind == "grade":
        return text.upper()
    return text

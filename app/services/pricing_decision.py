from __future__ import annotations

from decimal import Decimal

from app.enums import PricingSource, TradePhase
from app.models import PriceForecast, PricingDecision, Product, TradeWindow
from app.services.harvest_forecast import product_forecast_group_key


class PricingDecisionService:
    def decide(
        self,
        *,
        product: Product,
        trade_date,
        trade_window: TradeWindow,
        price_forecast: PriceForecast | None = None,
        break_even_price: Decimal | None = None,
        absolute_min_price: Decimal | None = None,
    ) -> PricingDecision:
        recommended_price = price_forecast.recommended_price if price_forecast is not None else product.recommended_price
        group_key = price_forecast.forecast_group_key if price_forecast is not None else product_forecast_group_key(product)

        if recommended_price is None:
            return PricingDecision(
                internal_sku=product.internal_sku,
                trade_date=trade_date,
                forecast_group_key=group_key,
                recommended_price=None,
                target_price=None,
                pricing_source=PricingSource.MANUAL_REVIEW_REQUIRED,
                requires_manual_review=True,
                review_reason="missing_recommended_price",
                decision_trace={"price_source": "missing", "trade_phase": trade_window.phase.value},
            )

        floor_price = absolute_min_price or product.base_cost
        if recommended_price < floor_price:
            return PricingDecision(
                internal_sku=product.internal_sku,
                trade_date=trade_date,
                forecast_group_key=group_key,
                recommended_price=recommended_price,
                target_price=floor_price,
                pricing_source=PricingSource.MANUAL_REVIEW_REQUIRED,
                requires_manual_review=True,
                review_reason="below_absolute_min_price",
                decision_trace={
                    "recommended_price": str(recommended_price),
                    "absolute_min_price": str(floor_price),
                    "trade_phase": trade_window.phase.value,
                },
            )

        if (
            trade_window.phase == TradePhase.CLEARANCE
            and break_even_price is not None
            and floor_price <= recommended_price < break_even_price
        ):
            return PricingDecision(
                internal_sku=product.internal_sku,
                trade_date=trade_date,
                forecast_group_key=group_key,
                recommended_price=recommended_price,
                target_price=recommended_price,
                pricing_source=PricingSource.MANUAL_REVIEW_REQUIRED,
                requires_manual_review=True,
                review_reason="below_break_even_price",
                decision_trace={
                    "recommended_price": str(recommended_price),
                    "break_even_price": str(break_even_price),
                    "absolute_min_price": str(floor_price),
                    "trade_phase": trade_window.phase.value,
                },
            )

        if (
            trade_window.phase == TradePhase.NORMAL_TRADING
            and break_even_price is not None
            and recommended_price < break_even_price
        ):
            recommended_price = break_even_price

        discount_cap = Decimal("0")
        if product.last_price is not None and recommended_price < product.last_price:
            if trade_window.phase == TradePhase.NORMAL_TRADING:
                discount_cap = Decimal("0.10")
            elif trade_window.phase == TradePhase.CLEARANCE:
                discount_cap = Decimal("0.20")
            allowed_min_price = product.last_price * (Decimal("1") - discount_cap)
            if discount_cap and recommended_price < allowed_min_price:
                recommended_price = allowed_min_price

        source = PricingSource.FORECAST_PRICE if price_forecast is not None else PricingSource.MANUAL_OVERRIDE
        return PricingDecision(
            internal_sku=product.internal_sku,
            trade_date=trade_date,
            forecast_group_key=group_key,
            recommended_price=price_forecast.recommended_price if price_forecast is not None else product.recommended_price,
            target_price=recommended_price.quantize(Decimal("0.01")),
            pricing_source=source,
            requires_manual_review=False,
            review_reason="",
            decision_trace={
                "price_source": "price_forecast" if price_forecast is not None else "product_recommended_price",
                "recommended_price": str(price_forecast.recommended_price)
                if price_forecast is not None
                else str(product.recommended_price),
                "target_price": str(recommended_price.quantize(Decimal("0.01"))),
                "trade_phase": trade_window.phase.value,
                "break_even_price": str(break_even_price) if break_even_price is not None else None,
                "absolute_min_price": str(floor_price),
                "last_price": str(product.last_price) if product.last_price is not None else None,
                "discount_cap": str(discount_cap),
            },
        )

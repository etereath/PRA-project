from __future__ import annotations

from app.enums import ShortageRisk, TradePhase
from app.models import InventoryPlan, ListingDecision, Product, TradeWindow


class ListingDecisionService:
    def decide(
        self,
        *,
        product: Product,
        inventory_plan: InventoryPlan,
        trade_window: TradeWindow,
        min_listing_qty: int = 1,
        manual_force_offline: bool = False,
        high_quality_risk: bool = False,
    ) -> ListingDecision:
        reason = "eligible_for_online"
        should_online = False
        should_offline = False

        if not product.sale_enabled:
            reason = "sale_enabled_false"
            should_offline = True
        elif trade_window.phase == TradePhase.CLOSED:
            reason = "trade_closed"
            should_offline = True
        elif manual_force_offline:
            reason = "manual_force_offline"
            should_offline = True
        elif high_quality_risk:
            reason = "high_quality_risk"
            should_offline = True
        elif inventory_plan.shortage_risk == ShortageRisk.HIGH:
            reason = "high_shortage_risk"
            should_offline = True
        elif inventory_plan.committable_qty < min_listing_qty:
            reason = "committable_qty_below_threshold"
            should_offline = True
        else:
            should_online = True

        return ListingDecision(
            internal_sku=product.internal_sku,
            trade_date=inventory_plan.trade_date,
            forecast_group_key=inventory_plan.forecast_group_key,
            committable_qty=inventory_plan.committable_qty,
            should_online=should_online,
            should_offline=should_offline,
            shortage_risk=inventory_plan.shortage_risk,
            reason=reason,
            decision_trace={
                "trade_phase": trade_window.phase.value,
                "sale_enabled": product.sale_enabled,
                "committable_qty": inventory_plan.committable_qty,
                "min_listing_qty": min_listing_qty,
                "shortage_risk": inventory_plan.shortage_risk.value,
                "reason": reason,
            },
        )

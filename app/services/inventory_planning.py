from __future__ import annotations

from app.enums import ShortageRisk, TaskActionType
from app.models import ColdStorageStatus, HarvestForecast, InventoryPlan, Product, ReviewRequirement
from app.services.inventory_strategies import InventoryStrategy, resolve_inventory_strategy


DEFAULT_FIELD_BUFFER_QTY = 50
DEFAULT_SAFETY_BUFFER_QTY = 0
DEFAULT_RESERVED_QTY = 0


class InventoryPlanningService:
    def __init__(self, strategy_name: str = "conservative_v1", strategy: InventoryStrategy | None = None) -> None:
        self.strategy = strategy or resolve_inventory_strategy(strategy_name)

    def build_inventory_plan(
        self,
        *,
        product: Product,
        forecast: HarvestForecast | None,
        trade_date,
        allocated_packing_capacity_qty: int,
        reserved_qty: int = DEFAULT_RESERVED_QTY,
        safety_buffer_qty: int = DEFAULT_SAFETY_BUFFER_QTY,
        field_buffer_qty: int = DEFAULT_FIELD_BUFFER_QTY,
    ) -> InventoryPlan:
        predicted_qty = forecast.predicted_harvest_qty if forecast is not None else 0
        actual_stock_qty = max(0, product.current_stock)
        result = self.strategy.compute(
            predicted_qty=predicted_qty,
            actual_stock_qty=actual_stock_qty,
            reserved_qty=reserved_qty,
            safety_buffer_qty=safety_buffer_qty,
            field_buffer_qty=field_buffer_qty,
            allocated_packing_capacity_qty=allocated_packing_capacity_qty,
        )
        group_key = forecast.forecast_group_key if forecast is not None else f"{product.product_name}::{product.grade}"
        return InventoryPlan(
            forecast_group_key=group_key,
            trade_date=trade_date,
            actual_stock_qty=actual_stock_qty,
            predicted_harvest_qty=predicted_qty,
            reserved_qty=reserved_qty,
            safety_buffer_qty=safety_buffer_qty,
            field_buffer_qty=field_buffer_qty,
            allocated_packing_capacity_qty=max(0, allocated_packing_capacity_qty),
            inventory_based_available_qty=result.inventory_based_available_qty,
            risk_adjusted_available_qty=result.risk_adjusted_available_qty,
            committable_qty=result.committable_qty,
            shortage_risk=result.shortage_risk,
            decision_trace={
                "actual_stock_qty": actual_stock_qty,
                "predicted_harvest_qty": predicted_qty,
                "reserved_qty": reserved_qty,
                "safety_buffer_qty": safety_buffer_qty,
                "field_buffer_qty": field_buffer_qty,
                "allocated_packing_capacity_qty": max(0, allocated_packing_capacity_qty),
                "inventory_based_available_qty": result.inventory_based_available_qty,
                "risk_adjusted_available_qty": result.risk_adjusted_available_qty,
                "committable_qty": result.committable_qty,
                "shortage_risk": result.shortage_risk.value,
                "inventory_strategy": result.strategy_name,
            },
        )

    def shortage_risk(
        self,
        *,
        sold_qty: int,
        predicted_harvest_qty: int,
        actual_stock_qty: int,
        field_buffer_qty: int,
    ) -> ShortageRisk:
        base_available = predicted_harvest_qty + actual_stock_qty
        risk_adjusted_available = base_available + field_buffer_qty
        if sold_qty <= base_available:
            return ShortageRisk.LOW
        if sold_qty <= risk_adjusted_available:
            return ShortageRisk.MANAGEABLE
        return ShortageRisk.HIGH

    def build_cold_storage_reviews(
        self,
        *,
        forecast_total_qty: int,
        committable_total_qty: int,
        cold_storage_status: ColdStorageStatus,
    ) -> list[ReviewRequirement]:
        estimated_remaining_qty = max(0, forecast_total_qty - committable_total_qty)
        if estimated_remaining_qty <= cold_storage_status.cold_storage_available_capacity:
            return []
        return [
            ReviewRequirement(
                task_type=TaskActionType.COLD_STORAGE_WARNING,
                internal_sku="__operation__",
                trade_date=cold_storage_status.trade_date,
                reason="estimated_remaining_exceeds_cold_storage_capacity",
                details={
                    "forecast_total_qty": forecast_total_qty,
                    "committable_total_qty": committable_total_qty,
                    "estimated_remaining_qty": estimated_remaining_qty,
                    "cold_storage_available_capacity": cold_storage_status.cold_storage_available_capacity,
                },
            )
        ]

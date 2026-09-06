from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.enums import ShortageRisk


@dataclass(slots=True)
class InventoryComputationResult:
    inventory_based_available_qty: int
    risk_adjusted_available_qty: int
    committable_qty: int
    shortage_risk: ShortageRisk
    strategy_name: str


class InventoryStrategy(ABC):
    name: str

    @abstractmethod
    def compute(
        self,
        *,
        predicted_qty: int,
        actual_stock_qty: int,
        reserved_qty: int,
        safety_buffer_qty: int,
        field_buffer_qty: int,
        allocated_packing_capacity_qty: int,
    ) -> InventoryComputationResult:
        raise NotImplementedError


class ConservativeInventoryStrategy(InventoryStrategy):
    name = "conservative_v1"

    def compute(
        self,
        *,
        predicted_qty: int,
        actual_stock_qty: int,
        reserved_qty: int,
        safety_buffer_qty: int,
        field_buffer_qty: int,
        allocated_packing_capacity_qty: int,
    ) -> InventoryComputationResult:
        inventory_based_available_qty = predicted_qty + actual_stock_qty - reserved_qty - safety_buffer_qty
        risk_adjusted_available_qty = (
            predicted_qty + actual_stock_qty + field_buffer_qty - reserved_qty - safety_buffer_qty
        )
        committable_qty = min(max(0, inventory_based_available_qty), max(0, allocated_packing_capacity_qty))
        shortage_risk = _shortage_risk(
            sold_qty=max(0, reserved_qty),
            predicted_qty=predicted_qty,
            actual_stock_qty=actual_stock_qty,
            field_buffer_qty=field_buffer_qty,
        )
        return InventoryComputationResult(
            inventory_based_available_qty=inventory_based_available_qty,
            risk_adjusted_available_qty=risk_adjusted_available_qty,
            committable_qty=committable_qty,
            shortage_risk=shortage_risk,
            strategy_name=self.name,
        )


class BalancedInventoryStrategy(InventoryStrategy):
    name = "balanced_v1"

    def compute(
        self,
        *,
        predicted_qty: int,
        actual_stock_qty: int,
        reserved_qty: int,
        safety_buffer_qty: int,
        field_buffer_qty: int,
        allocated_packing_capacity_qty: int,
    ) -> InventoryComputationResult:
        inventory_based_available_qty = predicted_qty + actual_stock_qty - reserved_qty - safety_buffer_qty
        risk_adjusted_available_qty = (
            predicted_qty + actual_stock_qty + field_buffer_qty - reserved_qty - safety_buffer_qty
        )
        buffer_share = max(0, field_buffer_qty // 2)
        balanced_available_qty = inventory_based_available_qty + buffer_share
        committable_qty = min(max(0, balanced_available_qty), max(0, allocated_packing_capacity_qty))
        shortage_risk = _shortage_risk(
            sold_qty=max(0, reserved_qty),
            predicted_qty=predicted_qty,
            actual_stock_qty=actual_stock_qty,
            field_buffer_qty=field_buffer_qty,
        )
        return InventoryComputationResult(
            inventory_based_available_qty=inventory_based_available_qty,
            risk_adjusted_available_qty=risk_adjusted_available_qty,
            committable_qty=committable_qty,
            shortage_risk=shortage_risk,
            strategy_name=self.name,
        )


INVENTORY_STRATEGIES: dict[str, InventoryStrategy] = {
    ConservativeInventoryStrategy.name: ConservativeInventoryStrategy(),
    BalancedInventoryStrategy.name: BalancedInventoryStrategy(),
}


def resolve_inventory_strategy(strategy_name: str) -> InventoryStrategy:
    try:
        return INVENTORY_STRATEGIES[strategy_name]
    except KeyError as exc:
        supported = ", ".join(sorted(INVENTORY_STRATEGIES))
        raise ValueError(f"unsupported inventory strategy '{strategy_name}', supported: {supported}") from exc


def _shortage_risk(
    *,
    sold_qty: int,
    predicted_qty: int,
    actual_stock_qty: int,
    field_buffer_qty: int,
) -> ShortageRisk:
    base_available = predicted_qty + actual_stock_qty
    risk_adjusted_available = base_available + field_buffer_qty
    if sold_qty <= base_available:
        return ShortageRisk.LOW
    if sold_qty <= risk_adjusted_available:
        return ShortageRisk.MANAGEABLE
    return ShortageRisk.HIGH

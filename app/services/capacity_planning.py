from __future__ import annotations

from collections.abc import Iterable
from math import ceil, floor

from app.models import HarvestForecast, PackingCapacityPlan, ReviewRequirement
from app.enums import TaskActionType


class CapacityPlanningService:
    def predicted_total_harvest_qty(self, forecasts: Iterable[HarvestForecast]) -> int:
        return sum(max(0, forecast.predicted_harvest_qty) for forecast in forecasts)

    def required_temp_workers(self, predicted_total_harvest_qty: int, plan: PackingCapacityPlan) -> int:
        extra_packing_qty = max(0, predicted_total_harvest_qty - plan.normal_packing_capacity_qty)
        if extra_packing_qty == 0:
            return 0
        return ceil(extra_packing_qty / plan.temp_worker_capacity_qty)

    def build_capacity_reviews(
        self,
        forecasts: list[HarvestForecast],
        plan: PackingCapacityPlan,
    ) -> list[ReviewRequirement]:
        predicted_total = self.predicted_total_harvest_qty(forecasts)
        required_workers = self.required_temp_workers(predicted_total, plan)
        if required_workers <= plan.confirmed_temp_worker_count:
            return []

        required_by = None
        if forecasts:
            import datetime as _dt

            trade_date = forecasts[0].target_trade_date
            required_by = _dt.datetime.combine(
                trade_date - _dt.timedelta(days=1),
                _dt.time(hour=20, minute=0),
            )

        details = {
            "predicted_total_harvest_qty": predicted_total,
            "normal_packing_capacity_qty": plan.normal_packing_capacity_qty,
            "confirmed_temp_worker_count": plan.confirmed_temp_worker_count,
            "required_temp_workers": required_workers,
        }
        return [
            ReviewRequirement(
                task_type=TaskActionType.CAPACITY_WARNING,
                internal_sku="__operation__",
                trade_date=plan.trade_date,
                reason="predicted_harvest_exceeds_confirmed_packing_capacity",
                required_by=required_by,
                details=details,
            ),
            ReviewRequirement(
                task_type=TaskActionType.LABOR_REQUIRED,
                internal_sku="__operation__",
                trade_date=plan.trade_date,
                reason="temp_labor_confirmation_required",
                required_by=required_by,
                details=details,
            ),
        ]

    def allocate_capacity(
        self,
        forecasts: Iterable[HarvestForecast],
        plan: PackingCapacityPlan,
    ) -> dict[str, int]:
        forecast_list = list(forecasts)
        capacity = max(0, plan.confirmed_packing_capacity_qty)
        if not forecast_list or capacity == 0:
            return {forecast.forecast_group_key: 0 for forecast in forecast_list}

        manual_allocations = {
            key: max(0, qty)
            for key, qty in plan.listing_quota.items()
            if key in {forecast.forecast_group_key for forecast in forecast_list}
        }
        if manual_allocations:
            return manual_allocations

        total = self.predicted_total_harvest_qty(forecast_list)
        if total <= 0:
            even_share = capacity // len(forecast_list)
            return {forecast.forecast_group_key: even_share for forecast in forecast_list}

        allocations: dict[str, int] = {}
        remainders: list[tuple[float, str]] = []
        allocated = 0
        for forecast in forecast_list:
            raw_share = capacity * max(0, forecast.predicted_harvest_qty) / total
            share = floor(raw_share)
            allocations[forecast.forecast_group_key] = share
            allocated += share
            remainders.append((raw_share - share, forecast.forecast_group_key))

        for _remainder, key in sorted(remainders, reverse=True)[: max(0, capacity - allocated)]:
            allocations[key] += 1
        return allocations

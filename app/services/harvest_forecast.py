from __future__ import annotations

from collections.abc import Iterable

from app.exceptions import ValidationError
from app.models import HarvestForecast, Product


def forecast_group_key(variety: str, grade: str) -> str:
    return f"{variety.strip()}::{grade.strip()}"


def product_forecast_group_key(product: Product) -> str:
    return forecast_group_key(product.product_name, product.grade)


class HarvestForecastService:
    def index_by_group(self, forecasts: Iterable[HarvestForecast]) -> dict[str, HarvestForecast]:
        indexed: dict[str, HarvestForecast] = {}
        for forecast in forecasts:
            key = forecast.forecast_group_key or forecast_group_key(forecast.variety, forecast.grade)
            if key in indexed:
                raise ValidationError(f"duplicate harvest forecast group: {key}")
            indexed[key] = forecast
        return indexed

    def forecast_for_product(
        self,
        product: Product,
        forecasts_by_group: dict[str, HarvestForecast],
    ) -> HarvestForecast | None:
        return forecasts_by_group.get(product_forecast_group_key(product))

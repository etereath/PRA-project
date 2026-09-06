from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from app.exceptions import ValidationError
from app.models import PriceForecast, Product
from app.services.harvest_forecast import product_forecast_group_key


class PriceForecastService:
    def index_by_group(self, forecasts: Iterable[PriceForecast]) -> dict[str, PriceForecast]:
        indexed: dict[str, PriceForecast] = {}
        for forecast in forecasts:
            key = forecast.forecast_group_key
            if key in indexed:
                raise ValidationError(f"duplicate price forecast group: {key}")
            indexed[key] = forecast
        return indexed

    def recommended_price_for_product(
        self,
        product: Product,
        forecasts_by_group: dict[str, PriceForecast],
    ) -> Decimal | None:
        forecast = forecasts_by_group.get(product_forecast_group_key(product))
        if forecast is not None:
            return forecast.recommended_price
        return product.recommended_price

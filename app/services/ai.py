from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from app.models import AISuggestionInput, AISuggestionResult


class AISuggestionProvider(Protocol):
    def suggest(self, request: AISuggestionInput) -> AISuggestionResult | None:
        """Return an AI pricing suggestion or None."""


class NullAISuggestionProvider:
    def suggest(self, request: AISuggestionInput) -> AISuggestionResult | None:
        return None


class MockAISuggestionProvider:
    """A deterministic mock provider for contract validation."""

    def suggest(self, request: AISuggestionInput) -> AISuggestionResult | None:
        if request.stock <= 0:
            return AISuggestionResult(
                suggested_price=None,
                confidence=Decimal("0.20"),
                reason="stock_depleted_no_price_change",
                model_version="mock-1.0",
            )
        multiplier = Decimal("1.03") if request.stock < 20 else Decimal("0.98")
        base = request.last_price or request.cost
        return AISuggestionResult(
            suggested_price=(base * multiplier).quantize(Decimal("0.01")),
            confidence=Decimal("0.62"),
            reason="mock_inventory_sensitivity",
            model_version="mock-1.0",
        )


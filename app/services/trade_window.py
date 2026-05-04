from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.enums import TradePhase
from app.models import TradeWindow


class TradeWindowService:
    def build(self, trade_date: date, now: datetime | None = None) -> TradeWindow:
        trade_open_at = datetime.combine(trade_date - timedelta(days=1), time(hour=23, minute=0))
        clearance_start_at = datetime.combine(trade_date, time(hour=15, minute=30))
        trade_close_at = datetime.combine(trade_date, time(hour=17, minute=0))
        current = self._normalize_now(now)

        if current >= trade_close_at:
            phase = TradePhase.CLOSED
        elif current >= clearance_start_at:
            phase = TradePhase.CLEARANCE
        else:
            phase = TradePhase.NORMAL_TRADING

        return TradeWindow(
            trade_date=trade_date,
            trade_open_at=trade_open_at,
            clearance_start_at=clearance_start_at,
            trade_close_at=trade_close_at,
            phase=phase,
        )

    def is_open(self, window: TradeWindow, now: datetime | None = None) -> bool:
        current = self._normalize_now(now)
        return window.trade_open_at <= current < window.trade_close_at

    def _normalize_now(self, now: datetime | None) -> datetime:
        current = now or datetime.now()
        if current.tzinfo is not None:
            return current.replace(tzinfo=None)
        return current

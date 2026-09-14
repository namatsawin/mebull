"""Event detection + meaningful-event gating (spec §12-13, §47).

Local deterministic processing decides whether an event is worth waking Claude for —
controlling token cost (spec §60) and avoiding overtrading. Scheduled reviews are always
meaningful; market/portfolio events must clear a threshold.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field


class EventType(StrEnum):
    # Scheduled (spec §13)
    MARKET_OPEN_REVIEW = "MARKET_OPEN_REVIEW"
    MID_SESSION_REVIEW = "MID_SESSION_REVIEW"
    MARKET_CLOSE_REVIEW = "MARKET_CLOSE_REVIEW"
    END_OF_DAY_REVIEW = "END_OF_DAY_REVIEW"
    WEEKLY_REVIEW = "WEEKLY_REVIEW"
    MONTHLY_REVIEW = "MONTHLY_REVIEW"
    # Market (spec §13)
    LARGE_PRICE_MOVE = "LARGE_PRICE_MOVE"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    VOLATILITY_CHANGE = "VOLATILITY_CHANGE"
    NEWS_EVENT = "NEWS_EVENT"
    EARNINGS_EVENT = "EARNINGS_EVENT"
    MACRO_EVENT = "MACRO_EVENT"
    # Portfolio (spec §13)
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    POSITION_CHANGED = "POSITION_CHANGED"
    BUYING_POWER_CHANGED = "BUYING_POWER_CHANGED"
    PORTFOLIO_DRAWDOWN_CHANGED = "PORTFOLIO_DRAWDOWN_CHANGED"
    # Research (spec §13)
    EXPERIMENT_COMPLETED = "EXPERIMENT_COMPLETED"
    UNEXPECTED_FAILURE = "UNEXPECTED_FAILURE"


_SCHEDULED = {
    EventType.MARKET_OPEN_REVIEW,
    EventType.MID_SESSION_REVIEW,
    EventType.MARKET_CLOSE_REVIEW,
    EventType.END_OF_DAY_REVIEW,
    EventType.WEEKLY_REVIEW,
    EventType.MONTHLY_REVIEW,
}

# Portfolio/research events that always warrant a look.
_ALWAYS = {
    EventType.ORDER_FILLED,
    EventType.ORDER_REJECTED,
    EventType.ORDER_CANCELLED,
    EventType.POSITION_CHANGED,
    EventType.EARNINGS_EVENT,
    EventType.MACRO_EVENT,
    EventType.EXPERIMENT_COMPLETED,
    EventType.UNEXPECTED_FAILURE,
}


class Event(BaseModel):
    type: EventType
    symbol: str | None = None
    payload: dict = Field(default_factory=dict)
    occurred_at: dt.datetime | None = None


class EventDetector:
    """Deterministic gate. Thresholds are conservative defaults (spec §12)."""

    def __init__(
        self,
        *,
        price_move_pct: float = 3.0,
        volume_spike_ratio: float = 3.0,
        volatility_change_pct: float = 25.0,
        drawdown_change_pct: float = 5.0,
    ) -> None:
        self.price_move_pct = price_move_pct
        self.volume_spike_ratio = volume_spike_ratio
        self.volatility_change_pct = volatility_change_pct
        self.drawdown_change_pct = drawdown_change_pct

    def is_meaningful(self, event: Event) -> bool:
        if event.type in _SCHEDULED or event.type in _ALWAYS:
            return True
        p = event.payload
        match event.type:
            case EventType.LARGE_PRICE_MOVE:
                return abs(float(p.get("change_pct", 0))) >= self.price_move_pct
            case EventType.VOLUME_SPIKE:
                return float(p.get("ratio", 0)) >= self.volume_spike_ratio
            case EventType.VOLATILITY_CHANGE:
                return abs(float(p.get("change_pct", 0))) >= self.volatility_change_pct
            case EventType.PORTFOLIO_DRAWDOWN_CHANGED:
                return abs(float(p.get("change_pct", 0))) >= self.drawdown_change_pct
            case EventType.NEWS_EVENT:
                return bool(p.get("high_impact", False))
            case EventType.BUYING_POWER_CHANGED:
                return abs(float(p.get("change_pct", 0))) >= 10.0
            case _:
                return False

"""In-memory portfolio state model (spec §53) computed from broker truth."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from apm.domain import Position


class PortfolioState(BaseModel):
    portfolio_id: str
    as_of: dt.datetime
    state_version: int

    portfolio_value: float
    cash: float
    buying_power: float

    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    long_exposure: float = 0.0
    short_exposure: float = 0.0
    sector_exposure: dict[str, float] = Field(default_factory=dict)
    asset_exposure: dict[str, float] = Field(default_factory=dict)
    drawdown: float | None = None

    positions: list[Position] = Field(default_factory=list)
    open_orders: int = 0

    def snapshot_summary(self) -> dict:
        """Compact, non-secret summary for decision context + journaling (spec §17, §45)."""
        return {
            "portfolio_id": self.portfolio_id,
            "as_of": self.as_of.isoformat(),
            "state_version": self.state_version,
            "portfolio_value": round(self.portfolio_value, 2),
            "cash": round(self.cash, 2),
            "buying_power": round(self.buying_power, 2),
            "gross_exposure": round(self.gross_exposure, 2),
            "net_exposure": round(self.net_exposure, 2),
            "open_orders": self.open_orders,
            "positions": [
                {
                    "symbol": p.symbol,
                    "instrument_type": p.instrument_type.value,
                    "quantity": p.quantity,
                    "avg_price": p.avg_price,
                    "market_price": p.market_price,
                    "market_value": round(p.market_value, 2),
                    "unrealized_pnl": round(p.unrealized_pnl, 2),
                }
                for p in self.positions
            ],
        }


def compute_exposures(positions: list[Position]) -> dict[str, float]:
    long_exp = sum(p.market_value for p in positions if p.quantity > 0)
    short_exp = sum(-p.market_value for p in positions if p.quantity < 0)
    return {
        "long_exposure": long_exp,
        "short_exposure": short_exp,
        "gross_exposure": long_exp + short_exp,
        "net_exposure": long_exp - short_exp,
    }

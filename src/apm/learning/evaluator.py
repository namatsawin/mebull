"""Learning Service (spec §20-25) — counterfactual evaluation + performance metrics.

Turns outcomes into evidence (spec §73). Two jobs here:
  1. Evaluate seeded counterfactuals: what a rejected candidate / WAIT would have returned
     (spec §20-22) — learning data without a virtual portfolio (spec §7).
  2. Compute performance metrics from CLOSED real trades (spec §24). Good decision != good
     outcome (spec §23), so metrics inform, they don't auto-promote strategies (spec §74).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from apm.config import get_settings
from apm.db import session_scope
from apm.db.models import Counterfactual, Trade
from apm.domain import Side
from apm.observability import get_logger
from apm.webull.adapter import WebullAdapter

log = get_logger("learning")


class LearningService:
    def __init__(self, adapter: WebullAdapter) -> None:
        self._adapter = adapter

    async def evaluate_counterfactuals(self, *, horizon: str = "current") -> int:
        """Price every unevaluated counterfactual at the current quote and record the
        return the candidate action would have produced. Returns count evaluated."""
        async with session_scope() as s:
            rows = (
                await s.scalars(
                    select(Counterfactual).where(Counterfactual.evaluated_at.is_(None))
                )
            ).all()
            if not rows:
                return 0

            symbols = sorted({r.candidate_symbol for r in rows if r.candidate_symbol})
            prices: dict[str, float] = {}
            if symbols:
                for q in await self._adapter.get_quotes(symbols):
                    prices[q.symbol] = q.price

            evaluated = 0
            now = dt.datetime.now(dt.UTC)
            for r in rows:
                p_now = prices.get(r.candidate_symbol)
                if p_now is None or not r.decision_time_price:
                    continue
                move = (p_now - r.decision_time_price) / r.decision_time_price
                # Direction of the considered action; WAIT/None assessed from the long side.
                sign = -1.0 if r.candidate_action == Side.SELL.value else 1.0
                r.future_prices = {**(r.future_prices or {}), horizon: p_now}
                r.counterfactual_return = move * sign
                r.evaluated_at = now
                evaluated += 1

        log.info("learning.counterfactuals", evaluated=evaluated)
        return evaluated

    async def compute_metrics(self) -> dict:
        """Performance metrics from closed trades (spec §24)."""
        settings = get_settings()
        async with session_scope() as s:
            trades = (
                await s.scalars(
                    select(Trade).where(
                        Trade.portfolio_id == settings.portfolio_id,
                        Trade.status == "CLOSED",
                    )
                )
            ).all()

        pnls = [t.net_pnl for t in trades if t.net_pnl is not None]
        n = len(pnls)
        if n == 0:
            return {"sample_size": 0}

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        return {
            "sample_size": n,
            "win_rate": len(wins) / n,
            "expectancy": sum(pnls) / n,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "payoff_ratio": (avg_win / abs(avg_loss)) if avg_loss else None,
            "profit_factor": (gross_win / gross_loss) if gross_loss else None,
            "total_net_pnl": sum(pnls),
        }

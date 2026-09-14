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
from apm.db.models import Decision as DecisionRow
from apm.domain import Side
from apm.observability import get_logger
from apm.webull.adapter import WebullAdapter

log = get_logger("learning")

# Words Claude may list as "opportunities considered" that are actions/states, not tickers.
_NON_TICKERS = {
    "WAIT", "HOLD", "CASH", "NONE", "N/A", "BUY", "SELL", "CLOSE", "SHORT",
    "REBALANCE", "RESEARCH", "EXPERIMENT",
}


def _is_ticker(sym: str) -> bool:
    """Cheap sanity filter so non-ticker strings never reach the quote API."""
    s = sym.strip().upper()
    if s in _NON_TICKERS:
        return False
    return s.replace(".", "").isalpha() and 1 <= len(s) <= 6


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

            # Claude sometimes lists non-tickers (e.g. "WAIT"/"CASH") among the opportunities
            # it considered; those must not be sent to the quote API (one bad symbol 417s the
            # whole batch and blocks all counterfactual learning).
            symbols = sorted(
                {
                    r.candidate_symbol
                    for r in rows
                    if r.candidate_symbol and _is_ticker(r.candidate_symbol)
                }
            )
            prices: dict[str, float] = {}
            if symbols:
                try:
                    for q in await self._adapter.get_quotes(symbols):
                        prices[q.symbol] = q.price
                except Exception as exc:  # noqa: BLE001 - never let learning break the loop
                    log.warning("learning.quotes_failed", error=str(exc), symbols=symbols)

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

    async def build_scorecard(self) -> dict:
        """A compact, deterministic 'self-track-record' fed into the decision context so the
        AI learns from its own history each cycle (no extra LLM call). High-signal, low-token:
        overall metrics + confidence calibration + performance by decision type + what the AI
        rejected/waited on. Everything here is computed from data already stored."""
        settings = get_settings()
        pid = settings.portfolio_id
        async with session_scope() as s:
            trades = (
                await s.scalars(
                    select(Trade).where(Trade.portfolio_id == pid, Trade.status == "CLOSED")
                )
            ).all()
            # Closed trades joined to the decision that spawned them (for type breakdown).
            type_rows = (
                await s.execute(
                    select(DecisionRow.decision_type, Trade.net_pnl)
                    .join(Trade, Trade.decision_id == DecisionRow.id)
                    .where(Trade.portfolio_id == pid, Trade.status == "CLOSED")
                )
            ).all()
            cfs = (
                await s.scalars(
                    select(Counterfactual).where(
                        Counterfactual.evaluated_at.is_not(None),
                        Counterfactual.counterfactual_return.is_not(None),
                    )
                )
            ).all()

        card: dict = {"overall": await self.compute_metrics()}

        # 1. Confidence calibration — does a high stated confidence actually win more?
        card["calibration"] = _calibration(
            [(t.claude_confidence, t.net_pnl) for t in trades]
        )

        # 2. Performance by decision type (BUY/SELL/CLOSE...).
        card["by_decision_type"] = _by_group(
            [(dtype, pnl) for dtype, pnl in type_rows]
        )

        # 3. Opportunities NOT taken (rejected candidates + WAITs) — is the AI leaving
        #    winners on the table (too conservative) or correctly avoiding losers?
        not_taken = [c.counterfactual_return for c in cfs if not c.chosen]
        if not_taken:
            would_win = [r for r in not_taken if r > 0.02]  # >2% move if it had acted
            card["opportunities_not_taken"] = {
                "sample": len(not_taken),
                "avg_return_if_taken": round(sum(not_taken) / len(not_taken), 4),
                "would_have_won_pct": round(len(would_win) / len(not_taken), 3),
            }
        return card


def _calibration(pairs: list[tuple[float | None, float | None]]) -> list[dict]:
    """Win-rate per stated-confidence bucket. Signal: is the AI's confidence meaningful?"""
    buckets = {"low<0.6": (0.0, 0.6), "mid0.6-0.8": (0.6, 0.8), "high>=0.8": (0.8, 1.01)}
    out = []
    for label, (lo, hi) in buckets.items():
        sample = [
            pnl
            for conf, pnl in pairs
            if conf is not None and pnl is not None and lo <= conf < hi
        ]
        if sample:
            wins = len([p for p in sample if p > 0])
            out.append(
                {"confidence": label, "n": len(sample), "win_rate": round(wins / len(sample), 3)}
            )
    return out


def _by_group(pairs: list[tuple[str, float | None]]) -> list[dict]:
    groups: dict[str, list[float]] = {}
    for key, pnl in pairs:
        if pnl is not None:
            groups.setdefault(key, []).append(pnl)
    out = []
    for key, pnls in groups.items():
        wins = len([p for p in pnls if p > 0])
        out.append(
            {
                "type": key,
                "n": len(pnls),
                "win_rate": round(wins / len(pnls), 3),
                "expectancy": round(sum(pnls) / len(pnls), 2),
            }
        )
    return out

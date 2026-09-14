"""Context Builder (spec §45-46) — assembles a relevant, compact DecisionContext."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from apm.config import get_settings
from apm.db import session_scope
from apm.db.models import Decision as DecisionRow
from apm.decision.context import ContextQuote, DecisionContext, Technicals
from apm.domain import Bar
from apm.learning.evaluator import LearningService
from apm.marketdata import minutes_to_close
from apm.memory.service import MemoryService
from apm.observability import get_logger
from apm.portfolio.state import PortfolioState
from apm.webull.adapter import WebullAdapter

log = get_logger("context")


class ContextBuilder:
    def __init__(
        self,
        adapter: WebullAdapter,
        memory: MemoryService,
        *,
        discovery=None,
        learning: LearningService | None = None,
    ) -> None:
        self._adapter = adapter
        self._memory = memory
        self._discovery = discovery
        self._learning = learning or LearningService(adapter)

    async def build(
        self, trigger: str, state: PortfolioState, *, extra_symbols: list[str] | None = None
    ) -> DecisionContext:
        settings = get_settings()
        watchlist = settings.watchlist_symbols
        # Symbols to quote = watchlist + current holdings + any discovery/extra symbols.
        symbols = list(
            dict.fromkeys(
                watchlist
                + [p.symbol for p in state.positions]
                + (extra_symbols or [])
            )
        )
        quotes: list[ContextQuote] = []
        market_snapshot: dict = {}
        if symbols:
            try:
                for q in await self._adapter.get_quotes(symbols):
                    quotes.append(
                        ContextQuote(symbol=q.symbol, price=q.price, change_pct=q.change_pct)
                    )
            except Exception as exc:  # noqa: BLE001 - market data may be down/unsubscribed
                # Degrade gracefully (spec §37): keep reasoning/journaling; the Safety Guard
                # blocks new orders when market data is unavailable.
                market_snapshot["market_data_error"] = str(exc)
                log.warning("context.quotes_unavailable", error=str(exc))

        # Deterministic technical levels per symbol (support/stop, trend, volatility) so the AI
        # can build a risk-defined entry. Degrades gracefully if bars are unavailable (§37).
        technicals = await self._technicals([q.symbol for q in quotes])
        breadth = _breadth(quotes)

        option_chains: dict[str, list[dict]] = {}
        if settings.options_enabled:
            option_chains = await self._affordable_options(watchlist, state.buying_power)

        discovery: list[dict] = []
        if self._discovery is not None:
            discovery = await self._discovery.scan(symbols)

        return DecisionContext(
            trigger=trigger,
            portfolio_id=state.portfolio_id,
            as_of=state.as_of.isoformat(),
            portfolio_state=state.snapshot_summary(),
            buying_power=state.buying_power,
            mandate=_mandate(settings),
            watchlist=watchlist,
            quotes=quotes,
            technicals=technicals,
            breadth=breadth,
            option_chains=option_chains,
            market_snapshot=market_snapshot,
            discovery=discovery,
            recent_decisions=await self._recent_decisions(state.portfolio_id),
            relevant_memories=await self._memory.recall(limit=10),
            known_failures=await self._memory.recall(category="FAILURE", limit=10),
            scorecard=await self._learning.build_scorecard(),
        )

    async def _technicals(self, symbols: list[str]) -> list[Technicals]:
        """Compute per-symbol technical levels from historical bars. Deterministic; degrades
        gracefully per symbol if bars are unavailable/unsubscribed (spec §37)."""
        out: list[Technicals] = []
        for sym in symbols:
            try:
                bars = await self._adapter.get_historical_bars(sym, timespan="d", count=60)
            except Exception as exc:  # noqa: BLE001 - bar data may be down/unsubscribed
                log.warning("context.bars_unavailable", symbol=sym, error=str(exc))
                continue
            t = _compute_technicals(sym, bars)
            if t is not None:
                out.append(t)
        return out

    async def _affordable_options(
        self, symbols: list[str], buying_power: float, per_symbol: int = 6
    ) -> dict[str, list[dict]]:
        """A compact slice of option contracts the account can actually afford, per symbol.
        Degrades gracefully if option data is unavailable/unsubscribed (403)."""
        out: dict[str, list[dict]] = {}
        for sym in symbols:
            try:
                chain = await self._adapter.get_option_chain(sym)
            except Exception as exc:  # noqa: BLE001 - option data may be down/unsubscribed
                log.warning("context.options_unavailable", symbol=sym, error=str(exc))
                continue
            affordable = [
                {
                    "right": c.right.value,
                    "strike": c.strike,
                    "expiry": c.expiry,
                    "mid": c.mid,
                    "cost": c.contract_cost,
                }
                for c in chain
                if c.contract_cost is not None and c.contract_cost <= buying_power
            ]
            affordable.sort(key=lambda x: x["cost"])
            if affordable:
                out[sym] = affordable[:per_symbol]
        return out

    async def _recent_decisions(self, portfolio_id: str, limit: int = 10) -> list[dict]:
        async with session_scope() as s:
            rows = (
                await s.scalars(
                    select(DecisionRow)
                    .where(DecisionRow.portfolio_id == portfolio_id)
                    .order_by(DecisionRow.timestamp.desc())
                    .limit(limit)
                )
            ).all()
        return [
            {
                "timestamp": r.timestamp.isoformat(),
                "decision_type": r.decision_type,
                "symbol": r.symbol,
                "confidence": r.confidence,
                "reasoning_summary": r.reasoning_summary,
            }
            for r in rows
        ]


def _mandate(settings) -> dict:
    """The intraday mandate for this cycle: style, intraday-flat rule, minutes to close, and
    per-trade risk guidance. Deterministic — from config + wall-clock (spec §45)."""
    mins = minutes_to_close(dt.datetime.now(dt.UTC))
    m = {
        "style": settings.trading_style,
        "intraday_only": settings.intraday_only,
        "max_risk_per_trade_pct": settings.max_risk_per_trade_pct,
        "minutes_to_close": mins,
        "must_flatten_within_minutes": settings.flatten_before_close_minutes,
    }
    if settings.intraday_only and mins is not None:
        # A clear, actionable instruction the model can act on near the bell.
        if mins <= settings.flatten_before_close_minutes:
            m["close_directive"] = (
                "CLOSE all open positions now — the session is about to end and no position "
                "may be held overnight."
            )
        elif mins <= settings.flatten_before_close_minutes * 3:
            m["close_directive"] = (
                "Session close is near — stop opening new risk and plan exits."
            )
        else:
            m["close_directive"] = "Intraday only: any position you open must be closed today."
    return m


def _sma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return round(sum(values[-n:]) / n, 2)


def _compute_technicals(symbol: str, bars: list[Bar]) -> Technicals | None:
    """Pure: derive support/resistance/trend/volatility from daily bars (spec §45-46)."""
    if not bars:
        return None
    closes = [b.close for b in bars]
    last = closes[-1]
    window = bars[-20:] if len(bars) >= 20 else bars
    support = round(min(b.low for b in window), 2)
    resistance = round(max(b.high for b in window), 2)
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)

    # ATR% proxy: mean daily range over last ~14 bars, as a % of last price.
    rng = [(b.high - b.low) for b in bars[-14:]]
    atr_pct = round((sum(rng) / len(rng)) / last * 100, 2) if rng and last else None

    momentum_5 = (
        round((last / closes[-6] - 1) * 100, 2) if len(closes) >= 6 and closes[-6] else None
    )

    trend = "flat"
    if sma20 is not None and sma50 is not None:
        if last > sma20 > sma50:
            trend = "up"
        elif last < sma20 < sma50:
            trend = "down"
    elif sma20 is not None:
        trend = "up" if last > sma20 else "down"

    return Technicals(
        symbol=symbol.upper(),
        last=round(last, 2),
        sma20=sma20,
        sma50=sma50,
        support=support,
        resistance=resistance,
        pct_to_support=round((last / support - 1) * 100, 2) if support else None,
        pct_to_resistance=round((resistance / last - 1) * 100, 2) if last else None,
        atr_pct=atr_pct,
        momentum_5=momentum_5,
        trend=trend,
    )


def _breadth(quotes: list[ContextQuote]) -> dict:
    """Pure: market breadth summary from quote change_pct — the corroboration read a PM uses."""
    changes = [q.change_pct for q in quotes if q.change_pct is not None]
    if not changes:
        return {}
    advancers = sum(1 for c in changes if c > 0)
    decliners = sum(1 for c in changes if c < 0)
    avg = round(sum(changes) / len(changes), 2)
    if advancers and not decliners:
        regime = "risk_on"
    elif decliners and not advancers:
        regime = "risk_off"
    elif avg > 0.1:
        regime = "risk_on_tilt"
    elif avg < -0.1:
        regime = "risk_off_tilt"
    else:
        regime = "mixed"
    return {
        "advancers": advancers,
        "decliners": decliners,
        "avg_change_pct": avg,
        "regime": regime,
    }

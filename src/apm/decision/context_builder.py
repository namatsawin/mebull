"""Context Builder (spec §45-46) — assembles a relevant, compact DecisionContext."""

from __future__ import annotations

from sqlalchemy import select

from apm.config import get_settings
from apm.db import session_scope
from apm.db.models import Decision as DecisionRow
from apm.decision.context import ContextQuote, DecisionContext
from apm.learning.evaluator import LearningService
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
                    quotes.append(ContextQuote(symbol=q.symbol, price=q.price))
            except Exception as exc:  # noqa: BLE001 - market data may be down/unsubscribed
                # Degrade gracefully (spec §37): keep reasoning/journaling; the Safety Guard
                # blocks new orders when market data is unavailable.
                market_snapshot["market_data_error"] = str(exc)
                log.warning("context.quotes_unavailable", error=str(exc))

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
            watchlist=watchlist,
            quotes=quotes,
            option_chains=option_chains,
            market_snapshot=market_snapshot,
            discovery=discovery,
            recent_decisions=await self._recent_decisions(state.portfolio_id),
            relevant_memories=await self._memory.recall(limit=10),
            known_failures=await self._memory.recall(category="FAILURE", limit=10),
            scorecard=await self._learning.build_scorecard(),
        )

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

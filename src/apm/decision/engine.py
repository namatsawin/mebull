"""Decision Engine (spec §14) — the core reasoning cycle.

Orchestrates one cycle: refresh portfolio state -> build relevant context -> ask Claude ->
validate -> journal (always, incl. WAIT) -> optionally hand a trade to the executor.

In analysis-only mode (M5) ``executor`` is None: decisions are journaled but no orders are
placed. At M7 an ExecutionCoordinator is injected; it still routes through the Safety Guard,
which independently decides whether execution is permitted (spec §79).
"""

from __future__ import annotations

from typing import Protocol

from apm.config import get_settings
from apm.decision.context import DecisionContext
from apm.decision.context_builder import ContextBuilder
from apm.decision.contract import Decision
from apm.decision.provider import ClaudeProvider
from apm.journal.service import JournalService
from apm.memory.service import MemoryService
from apm.observability import get_logger
from apm.portfolio.service import PortfolioService

log = get_logger("engine")


class ExecutionCoordinator(Protocol):
    async def execute(
        self, decision: Decision, *, context: DecisionContext, decision_id: str
    ) -> object | None: ...


class DecisionEngine:
    def __init__(
        self,
        *,
        portfolio: PortfolioService,
        context_builder: ContextBuilder,
        provider: ClaudeProvider,
        journal: JournalService,
        memory: MemoryService,
        executor: ExecutionCoordinator | None = None,
    ) -> None:
        self._portfolio = portfolio
        self._context_builder = context_builder
        self._provider = provider
        self._journal = journal
        self._memory = memory
        self._executor = executor

    async def run_cycle(self, trigger: str) -> Decision:
        settings = get_settings()
        await self._portfolio.ensure_portfolio()
        state = await self._portfolio.build_state()

        context = await self._context_builder.build(trigger, state)
        decision = await self._provider.analyze(context)
        decision.portfolio_id = decision.portfolio_id or settings.portfolio_id

        memory_version = await self._memory.current_version()
        decision_id = await self._journal.write_decision(
            decision,
            portfolio_state=state,
            market_snapshot=context.market_snapshot,
            memory_version=memory_version,
            candidate_prices=context.candidate_prices(),
        )

        log.info(
            "engine.decision",
            trigger=trigger,
            decision_id=decision_id,
            type=decision.decision_type.value,
            symbol=decision.symbol,
        )

        order_intent = decision.decision_type.places_order
        order_placed = False
        block_reason: str | None = None
        if order_intent and self._executor is not None:
            result = await self._executor.execute(
                decision, context=context, decision_id=decision_id
            )
            if result is not None:
                order_placed = bool(getattr(result, "placed", False))
                auth = getattr(result, "authorization", None)
                if not order_placed and auth is not None:
                    block_reason = getattr(auth, "reason", None)
        elif order_intent:
            log.info("engine.analysis_only", decision_id=decision_id, note="order not placed")
            block_reason = "analysis-only (no executor)"

        # Full audit trail of this cycle's reasoning — order or not (spec §17). Best-effort.
        try:
            await self._journal.write_audit(
                trigger=trigger,
                decision=decision,
                context_dict=context.to_prompt_dict(),
                decision_id=decision_id,
                order_intent=order_intent,
                order_placed=order_placed,
                block_reason=block_reason,
            )
        except Exception as exc:  # noqa: BLE001 - audit must never break the loop
            log.warning("engine.audit_failed", error=str(exc))

        return decision

"""TradingApp — wires the always-on infrastructure around the decision engine (spec §11).

The AI decides freely: every trigger runs one full decision cycle and Claude chooses
BUY/SELL/CLOSE/HOLD/WAIT/... on its own. There is no meaningful-event gate — the
orchestrator simply calls ``run_once`` on a fixed interval (APM_DECISION_INTERVAL_SECONDS).

Analysis-only until an executor is injected; the executor still routes through the Safety
Guard, which independently decides whether execution is permitted (spec §79).
"""

from __future__ import annotations

from apm.config import Settings, get_settings
from apm.decision.context_builder import ContextBuilder
from apm.decision.contract import Decision
from apm.decision.engine import DecisionEngine, ExecutionCoordinator
from apm.decision.provider import build_provider
from apm.execution.coordinator import ExecutionCoordinator as RealCoordinator
from apm.execution.service import ExecutionService
from apm.journal.service import JournalService
from apm.learning.evaluator import LearningService
from apm.memory.service import MemoryService
from apm.observability import get_logger
from apm.portfolio.service import PortfolioService
from apm.reconcile.service import ReconciliationService
from apm.safety.guard import SafetyGuard
from apm.webull import build_adapter

log = get_logger("app")


class TradingApp:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        executor: ExecutionCoordinator | None = None,
        enable_execution: bool = True,
    ) -> None:
        self._settings = settings or get_settings()
        self._adapter = build_adapter(self._settings)
        self._portfolio = PortfolioService(self._adapter)
        self._memory = MemoryService(self._settings.portfolio_id)
        self._journal = JournalService()
        self._reconcile = ReconciliationService(self._adapter, self._portfolio)
        self._learning = LearningService(self._adapter)

        # Execution stack: decisions that place orders route through the Safety Guard
        # (spec §30, §34). Every mode uses it; the guard gates whether REAL is permitted.
        if executor is None and enable_execution:
            guard = SafetyGuard(settings=self._settings)
            exec_service = ExecutionService(
                adapter=self._adapter,
                guard=guard,
                journal=self._journal,
                portfolio=self._portfolio,
                reconcile=self._reconcile,
            )
            executor = RealCoordinator(exec_service, self._portfolio, self._adapter)

        self._executor = executor
        self._context_builder = ContextBuilder(self._adapter, self._memory, learning=self._learning)
        self._engine = DecisionEngine(
            portfolio=self._portfolio,
            context_builder=self._context_builder,
            provider=build_provider(),
            journal=self._journal,
            memory=self._memory,
            executor=executor,
        )
        # Quant mode (B): the AI supervisor sets policy a few times/day; the engine decides
        # deterministically each cycle from that policy.
        self._supervisor = None
        if self._settings.strategy_mode == "quant":
            from apm.supervisor import SupervisorService

            self._supervisor = SupervisorService()

    @property
    def reconcile(self) -> ReconciliationService:
        return self._reconcile

    async def start(self) -> None:
        await self._portfolio.ensure_portfolio()
        await self._reconcile.reconcile(reason="startup")
        log.info(
            "app.started",
            execution_mode=self._settings.execution_mode.value,
            interval_seconds=self._settings.decision_interval_seconds,
        )

    async def run_once(self, trigger: str = "PERIODIC") -> Decision:
        """Run one full decision cycle: the AI decides freely, we journal, and (if it chose
        to trade) the order goes through the Safety Guard. Then keep learning up to date."""
        decision = await self._engine.run_cycle(trigger)
        try:
            await self._learning.evaluate_counterfactuals()
        except Exception as exc:  # noqa: BLE001 - learning must never break the loop
            log.warning("learning.failed", error=str(exc))
        return decision

    @property
    def supervisor_enabled(self) -> bool:
        return self._supervisor is not None

    async def run_supervisor(self) -> None:
        """Refresh the SupervisorPolicy (quant mode only). A few AI calls/day — not per cycle."""
        if self._supervisor is None:
            return
        await self._portfolio.ensure_portfolio()
        state = await self._portfolio.build_state()
        context = await self._context_builder.build("SUPERVISOR", state)
        policy = await self._supervisor.refresh(context)
        log.info(
            "app.supervisor_refreshed",
            regime=policy.regime,
            trade_today=policy.trade_today,
            risk_multiplier=policy.risk_multiplier,
        )

    async def flatten_positions(self, *, reason: str = "eod-flatten") -> int:
        """Deterministically close all open positions (intraday-flat guarantee). No-op if the
        executor doesn't support it. Routes through the Safety Guard."""
        flatten = getattr(self._executor, "flatten_all", None)
        if flatten is None:
            return 0
        count = await flatten(reason=reason)
        if count:
            log.info("app.flattened", count=count, reason=reason)
        return count

    async def stop(self) -> None:
        log.info("app.stopped")

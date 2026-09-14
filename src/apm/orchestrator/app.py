"""TradingApp — wires the always-on infrastructure around the decision engine (spec §11).

One serialized reasoning path: scheduled reviews and market/portfolio events are gated by
the EventDetector, and meaningful ones are queued and processed one-at-a-time by the engine
(spec §12). Analysis-only until an executor is injected (M7); the executor still routes
through the Safety Guard (spec §79).
"""

from __future__ import annotations

import asyncio

from apm.config import Settings, get_settings
from apm.db import session_scope
from apm.db.models import SystemEvent
from apm.decision.context_builder import ContextBuilder
from apm.decision.engine import DecisionEngine
from apm.decision.provider import build_provider
from apm.events.detector import Event, EventDetector
from apm.execution.coordinator import ExecutionCoordinator
from apm.execution.service import ExecutionService
from apm.journal.service import JournalService
from apm.learning.review import PERIOD_FOR_EVENT, ReviewService
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
        self._review = ReviewService(self._adapter)
        self._detector = EventDetector()

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
            executor = ExecutionCoordinator(exec_service, self._portfolio)

        self._engine = DecisionEngine(
            portfolio=self._portfolio,
            context_builder=ContextBuilder(self._adapter, self._memory),
            provider=build_provider(),
            journal=self._journal,
            memory=self._memory,
            executor=executor,
        )
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    @property
    def reconcile(self) -> ReconciliationService:
        return self._reconcile

    async def start(self) -> None:
        await self._portfolio.ensure_portfolio()
        await self._reconcile.reconcile(reason="startup")
        self._worker = asyncio.create_task(self._run_worker(), name="decision-worker")
        log.info("app.started", execution_mode=self._settings.execution_mode.value)

    async def submit(self, event: Event) -> bool:
        """Gate an event; enqueue if meaningful (spec §12). Returns whether it was queued."""
        meaningful = self._detector.is_meaningful(event)
        await self._record_event(event, meaningful)
        if meaningful:
            await self._queue.put(event)
        else:
            log.info("event.ignored", type=event.type.value, symbol=event.symbol)
        return meaningful

    async def wait_idle(self) -> None:
        """Block until all queued events have been processed (tests / graceful drain)."""
        await self._queue.join()

    async def _record_event(self, event: Event, meaningful: bool) -> None:
        try:
            async with session_scope() as s:
                s.add(
                    SystemEvent(
                        event_type=f"event:{event.type.value}",
                        severity="info",
                        message="queued" if meaningful else "ignored",
                        payload={"symbol": event.symbol, "meaningful": meaningful, **event.payload},
                    )
                )
        except Exception as exc:  # noqa: BLE001 - observability must never crash the path
            log.warning("event.record_failed", error=str(exc))

    async def _run_worker(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await self._engine.run_cycle(event.type.value)
                period = PERIOD_FOR_EVENT.get(event.type.value)
                if period:
                    await self._review.generate(period)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the loop
                log.error("cycle.failed", type=event.type.value, error=str(exc))
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        log.info("app.stopped")

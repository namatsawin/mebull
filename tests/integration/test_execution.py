import pytest
from sqlalchemy import func, select

from apm.config import ExecutionMode, Settings
from apm.db import session_scope
from apm.db.models import Execution, OrderRecord, Trade
from apm.decision.context_builder import ContextBuilder
from apm.decision.contract import Decision, DecisionType
from apm.decision.engine import DecisionEngine
from apm.decision.provider import MockProvider
from apm.execution.coordinator import ExecutionCoordinator
from apm.execution.service import ExecutionService
from apm.journal.service import JournalService
from apm.memory.service import MemoryService
from apm.portfolio.service import PortfolioService
from apm.reconcile.service import ReconciliationService
from apm.safety.guard import SafetyGuard
from apm.webull.mock import MockWebullAdapter

pytestmark = pytest.mark.asyncio


def _stack(adapter, canned, *, mode=ExecutionMode.SANDBOX, trading_enabled=True):
    settings = Settings(execution_mode=mode, trading_enabled=trading_enabled)
    portfolio = PortfolioService(adapter)
    journal = JournalService()
    reconcile = ReconciliationService(adapter, portfolio)
    guard = SafetyGuard(settings=settings)
    execsvc = ExecutionService(
        adapter=adapter, guard=guard, journal=journal,
        portfolio=portfolio, reconcile=reconcile,
    )
    coord = ExecutionCoordinator(execsvc, portfolio)
    memory = MemoryService()
    engine = DecisionEngine(
        portfolio=portfolio,
        context_builder=ContextBuilder(adapter, memory),
        provider=MockProvider(canned=canned),
        journal=journal,
        memory=memory,
        executor=coord,
    )
    return engine, portfolio


async def test_buy_decision_executes_end_to_end(clean_db):
    adapter = MockWebullAdapter(starting_cash=100_000)
    adapter.set_quote("NVDA", 100.0)
    canned = Decision(
        decision_type=DecisionType.BUY, symbol="NVDA", quantity=10,
        confidence=0.9, reasoning_summary="breakout",
        opportunities_considered=["NVDA"], selected_opportunity="NVDA",
    )
    engine, portfolio = _stack(adapter, canned)
    await portfolio.ensure_portfolio()

    await engine.run_cycle("EVENT")

    # Broker position created.
    positions = {p.symbol: p for p in await adapter.get_positions()}
    assert positions["NVDA"].quantity == 10

    async with session_scope() as s:
        orders = await s.scalar(select(func.count()).select_from(OrderRecord))
        execs = await s.scalar(select(func.count()).select_from(Execution))
        trades = (await s.scalars(select(Trade))).all()
    assert orders == 1
    assert execs == 1
    assert len(trades) == 1
    assert trades[0].status == "OPEN"
    assert trades[0].symbol == "NVDA"


async def test_order_blocked_by_insufficient_buying_power(clean_db):
    adapter = MockWebullAdapter(starting_cash=500)  # can't afford 10 @ 100
    adapter.set_quote("NVDA", 100.0)
    canned = Decision(
        decision_type=DecisionType.BUY, symbol="NVDA", quantity=10,
        confidence=0.9, reasoning_summary="too big",
        opportunities_considered=["NVDA"], selected_opportunity="NVDA",
    )
    engine, portfolio = _stack(adapter, canned)
    await portfolio.ensure_portfolio()

    await engine.run_cycle("EVENT")

    assert await adapter.get_positions() == []  # nothing placed
    async with session_scope() as s:
        orders = await s.scalar(select(func.count()).select_from(OrderRecord))
    assert orders == 0


async def test_buy_then_close_realizes_trade(clean_db):
    adapter = MockWebullAdapter(starting_cash=100_000)
    adapter.set_quote("NVDA", 100.0)
    buy = Decision(
        decision_type=DecisionType.BUY, symbol="NVDA", quantity=10,
        confidence=0.9, reasoning_summary="entry",
        opportunities_considered=["NVDA"], selected_opportunity="NVDA",
    )
    engine, portfolio = _stack(adapter, buy)
    await portfolio.ensure_portfolio()
    await engine.run_cycle("EVENT")

    # Price moves up, then CLOSE the position.
    adapter.set_quote("NVDA", 120.0)
    close = Decision(
        decision_type=DecisionType.CLOSE, symbol="NVDA",
        confidence=0.9, reasoning_summary="take profit",
    )
    engine2, _ = _stack(adapter, close)
    await engine2.run_cycle("EVENT")

    assert await adapter.get_positions() == []  # flat
    async with session_scope() as s:
        trade = (await s.scalars(select(Trade))).all()[0]
    assert trade.status == "CLOSED"
    assert trade.net_pnl == pytest.approx((120 - 100) * 10)  # +200


async def test_idempotent_execution_same_decision(clean_db):
    import datetime as dt

    from apm.decision.context import DecisionContext
    from apm.portfolio.state import PortfolioState

    adapter = MockWebullAdapter(starting_cash=100_000)
    adapter.set_quote("NVDA", 100.0)
    canned = Decision(
        decision_type=DecisionType.BUY, symbol="NVDA", quantity=10,
        confidence=0.9, reasoning_summary="entry",
        opportunities_considered=["NVDA"], selected_opportunity="NVDA",
    )
    settings = Settings(execution_mode=ExecutionMode.SANDBOX, trading_enabled=True)
    portfolio = PortfolioService(adapter)
    journal = JournalService()
    reconcile = ReconciliationService(adapter, portfolio)
    guard = SafetyGuard(settings=settings)
    execsvc = ExecutionService(
        adapter=adapter, guard=guard, journal=journal,
        portfolio=portfolio, reconcile=reconcile,
    )
    coord = ExecutionCoordinator(execsvc, portfolio)
    await portfolio.ensure_portfolio()

    # A real decision row is required (order_record.decision_id FK).
    state = PortfolioState(
        portfolio_id="main-portfolio", as_of=dt.datetime.now(dt.UTC), state_version=0,
        portfolio_value=100_000, cash=100_000, buying_power=100_000,
    )
    decision_id = await journal.write_decision(canned, portfolio_state=state)

    ctx = DecisionContext(
        trigger="T", portfolio_id="main-portfolio", as_of="2026-01-02T00:00:00+00:00"
    )
    # Same decision_id twice -> same client_order_id -> second is a no-op at the broker.
    await coord.execute(canned, context=ctx, decision_id=decision_id)
    await coord.execute(canned, context=ctx, decision_id=decision_id)

    async with session_scope() as s:
        orders = await s.scalar(select(func.count()).select_from(OrderRecord))
    assert orders == 1  # idempotent (spec §33)
    positions = {p.symbol: p for p in await adapter.get_positions()}
    assert positions["NVDA"].quantity == 10  # not doubled

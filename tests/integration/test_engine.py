import pytest
from sqlalchemy import func, select

from apm.db import session_scope
from apm.db.models import Counterfactual
from apm.db.models import Decision as DecisionRow
from apm.decision.context_builder import ContextBuilder
from apm.decision.contract import Decision, DecisionType
from apm.decision.engine import DecisionEngine
from apm.decision.provider import MockProvider
from apm.journal.service import JournalService
from apm.memory.service import MemoryService
from apm.portfolio.service import PortfolioService
from apm.webull.mock import MockWebullAdapter

pytestmark = pytest.mark.asyncio


def _engine(adapter, provider):
    memory = MemoryService("main-portfolio")
    return DecisionEngine(
        portfolio=PortfolioService(adapter),
        context_builder=ContextBuilder(adapter, memory),
        provider=provider,
        journal=JournalService(),
        memory=memory,
        executor=None,  # analysis-only
    )


async def test_run_cycle_journals_wait_decision(clean_db):
    adapter = MockWebullAdapter()
    adapter.set_quote("NVDA", 150.0)
    engine = _engine(adapter, MockProvider())

    decision = await engine.run_cycle("MARKET_OPEN_REVIEW")
    assert decision.decision_type is DecisionType.WAIT

    async with session_scope() as s:
        n = await s.scalar(select(func.count()).select_from(DecisionRow))
        cfs = await s.scalar(select(func.count()).select_from(Counterfactual))
    assert n == 1
    assert cfs >= 1  # counterfactuals seeded for considered symbols


async def test_run_cycle_analysis_only_does_not_place_orders(clean_db):
    adapter = MockWebullAdapter(starting_cash=100_000)
    adapter.set_quote("NVDA", 100.0)
    canned = Decision(
        decision_type=DecisionType.BUY, symbol="NVDA", quantity=10,
        confidence=0.9, reasoning_summary="breakout", selected_opportunity="NVDA",
        opportunities_considered=["NVDA"],
    )
    engine = _engine(adapter, MockProvider(canned=canned))

    decision = await engine.run_cycle("EVENT")
    assert decision.decision_type is DecisionType.BUY

    # Analysis-only: journaled, but NO order was placed at the broker.
    assert len(await adapter.get_open_orders()) == 0
    positions = await adapter.get_positions()
    assert positions == []
    async with session_scope() as s:
        n = await s.scalar(select(func.count()).select_from(DecisionRow))
    assert n == 1

import datetime as dt

import pytest
from sqlalchemy import func, select

from apm.db import session_scope
from apm.db.models import Counterfactual, DecisionCandidate
from apm.db.models import Decision as DecisionRow
from apm.decision.contract import Decision, DecisionType
from apm.journal.service import JournalService
from apm.memory.service import MemoryService
from apm.portfolio.state import PortfolioState

pytestmark = pytest.mark.asyncio


def _state():
    return PortfolioState(
        portfolio_id="main-portfolio",
        as_of=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
        state_version=1,
        portfolio_value=100_000,
        cash=100_000,
        buying_power=100_000,
    )


async def test_wait_decision_records_candidates_and_counterfactuals(clean_db):
    d = Decision(
        decision_type=DecisionType.WAIT,
        confidence=0.72,
        reasoning_summary="No opportunity offers sufficient edge.",
        opportunities_considered=["NVDA", "QQQ", "SPY"],
        rejected_opportunities=[
            {"symbol": "NVDA", "reason": "insufficient confirmation"},
            {"symbol": "QQQ", "reason": "poor risk/reward"},
        ],
    )
    journal = JournalService()
    decision_id = await journal.write_decision(
        d, portfolio_state=_state(), candidate_prices={"NVDA": 150.0, "QQQ": 400.0, "SPY": 500.0}
    )

    async with session_scope() as s:
        row = await s.get(DecisionRow, decision_id)
        assert row.decision_type == "WAIT"
        assert row.portfolio_state_snapshot["portfolio_value"] == 100_000
        cands = (await s.scalars(
            select(DecisionCandidate).where(DecisionCandidate.decision_id == decision_id)
        )).all()
        cfs = (await s.scalars(
            select(Counterfactual).where(Counterfactual.decision_id == decision_id)
        )).all()
    assert len(cands) == 3
    assert len(cfs) == 3
    nvda = next(c for c in cands if c.symbol == "NVDA")
    assert nvda.reason_for_rejection == "insufficient confirmation"
    assert nvda.decision_time_price == 150.0


async def test_buy_decision_persists(clean_db):
    d = Decision(
        decision_type=DecisionType.BUY, symbol="NVDA", quantity=10,
        confidence=0.8, reasoning_summary="volatility breakout",
        opportunities_considered=["NVDA"], selected_opportunity="NVDA",
    )
    journal = JournalService()
    decision_id = await journal.write_decision(d, portfolio_state=_state())
    async with session_scope() as s:
        row = await s.get(DecisionRow, decision_id)
        assert row.symbol == "NVDA"
        assert row.action == "BUY"
        assert row.quantity == 10


async def test_memory_versions_and_recall(clean_db):
    mem = MemoryService("main-portfolio")
    assert await mem.current_version() == "M0"

    v1 = await mem.remember("FAILURE", "vol-earnings", "Strategy X poor in high-vol earnings.")
    assert v1 == "M1"
    v2 = await mem.remember("FAILURE", "vol-earnings", "Refined: only when IV rank > 80.",
                            reason="added nuance")
    assert v2 == "M2"  # revision appended, not overwritten

    async with session_scope() as s:
        from apm.db.models import Memory, MemoryRevision
        n_items = await s.scalar(select(func.count()).select_from(Memory))
        n_revs = await s.scalar(select(func.count()).select_from(MemoryRevision))
    assert n_items == 1  # same key -> one item
    assert n_revs == 2   # two revisions

    hits = await mem.recall(category="FAILURE", query="IV rank")
    assert len(hits) == 1
    assert "IV rank" in hits[0]["content"]

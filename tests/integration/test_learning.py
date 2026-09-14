import datetime as dt

import pytest
from sqlalchemy import select

from apm.db import session_scope
from apm.db.models import Counterfactual, Trade
from apm.decision.contract import Decision, DecisionType
from apm.journal.service import JournalService
from apm.learning.evaluator import LearningService
from apm.portfolio.state import PortfolioState
from apm.webull.mock import MockWebullAdapter

pytestmark = pytest.mark.asyncio


def _state():
    return PortfolioState(
        portfolio_id="main-portfolio", as_of=dt.datetime.now(dt.UTC), state_version=1,
        portfolio_value=100_000, cash=100_000, buying_power=100_000,
    )


async def test_counterfactual_evaluation_computes_return(clean_db):
    # WAIT decision that considered NVDA at 100; price later rises to 150.
    d = Decision(
        decision_type=DecisionType.WAIT, confidence=0.7,
        reasoning_summary="waited",
        opportunities_considered=["NVDA"],
        rejected_opportunities=[{"symbol": "NVDA", "reason": "no confirmation"}],
    )
    await JournalService().write_decision(
        d, portfolio_state=_state(), candidate_prices={"NVDA": 100.0}
    )

    adapter = MockWebullAdapter()
    adapter.set_quote("NVDA", 150.0)
    learning = LearningService(adapter)

    n = await learning.evaluate_counterfactuals()
    assert n == 1

    async with session_scope() as s:
        cf = (await s.scalars(select(Counterfactual))).all()[0]
    assert cf.counterfactual_return == pytest.approx(0.5)  # +50% missed
    assert cf.evaluated_at is not None

    # Idempotent: already-evaluated rows are not re-evaluated.
    assert await learning.evaluate_counterfactuals() == 0


async def test_metrics_from_closed_trades(clean_db):
    # Two closed trades: +200 win, -50 loss.
    async with session_scope() as s:
        now = dt.datetime.now(dt.UTC)
        s.add(Trade(portfolio_id="main-portfolio", symbol="NVDA", entry_time=now,
                    entry_price=100, exit_time=now, exit_price=120, quantity=10,
                    capital_allocated=1000, net_pnl=200.0, status="CLOSED"))
        s.add(Trade(portfolio_id="main-portfolio", symbol="AMD", entry_time=now,
                    entry_price=50, exit_time=now, exit_price=45, quantity=10,
                    capital_allocated=500, net_pnl=-50.0, status="CLOSED"))

    metrics = await LearningService(MockWebullAdapter()).compute_metrics()
    assert metrics["sample_size"] == 2
    assert metrics["win_rate"] == pytest.approx(0.5)
    assert metrics["expectancy"] == pytest.approx(75.0)
    assert metrics["total_net_pnl"] == pytest.approx(150.0)
    assert metrics["profit_factor"] == pytest.approx(4.0)

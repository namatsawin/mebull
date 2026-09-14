import datetime as dt

import pytest

from apm.db import session_scope
from apm.db.models import Counterfactual, Trade
from apm.db.models import Decision as DecisionRow
from apm.learning.evaluator import LearningService
from apm.webull.mock import MockWebullAdapter

pytestmark = pytest.mark.asyncio


async def _seed():
    now = dt.datetime.now(dt.UTC)
    async with session_scope() as s:
        d_win = DecisionRow(
            portfolio_id="main-portfolio", timestamp=now, decision_type="BUY",
            confidence=0.9, reasoning_summary="win",
        )
        d_loss = DecisionRow(
            portfolio_id="main-portfolio", timestamp=now, decision_type="BUY",
            confidence=0.5, reasoning_summary="loss",
        )
        s.add_all([d_win, d_loss])
        await s.flush()
        # Winning trade (high confidence), losing trade (low confidence).
        s.add(Trade(portfolio_id="main-portfolio", decision_id=d_win.id, symbol="NVDA",
                    entry_time=now, entry_price=100, exit_time=now, exit_price=120,
                    quantity=10, net_pnl=200.0, claude_confidence=0.9, status="CLOSED"))
        s.add(Trade(portfolio_id="main-portfolio", decision_id=d_loss.id, symbol="AMD",
                    entry_time=now, entry_price=50, exit_time=now, exit_price=45,
                    quantity=10, net_pnl=-50.0, claude_confidence=0.5, status="CLOSED"))
        # A rejected opportunity that WOULD have won +8% (AI left it on the table).
        s.add(Counterfactual(decision_id=d_win.id, candidate_symbol="TSLA", chosen=False,
                             decision_time_price=200.0, counterfactual_return=0.08,
                             evaluated_at=now))


async def test_scorecard_has_high_signal_sections(clean_db):
    await _seed()
    card = await LearningService(MockWebullAdapter()).build_scorecard()

    assert card["overall"]["sample_size"] == 2
    assert card["overall"]["win_rate"] == pytest.approx(0.5)

    # Calibration: high-confidence bucket won, low-confidence bucket lost.
    cal = {b["confidence"]: b for b in card["calibration"]}
    assert cal["high>=0.8"]["win_rate"] == pytest.approx(1.0)
    assert cal["low<0.6"]["win_rate"] == pytest.approx(0.0)

    # By decision type: BUY present with both trades.
    buy = next(b for b in card["by_decision_type"] if b["type"] == "BUY")
    assert buy["n"] == 2

    # Opportunities not taken: one rejected winner (+8%).
    ont = card["opportunities_not_taken"]
    assert ont["sample"] == 1
    assert ont["would_have_won_pct"] == pytest.approx(1.0)


async def test_scorecard_empty_when_no_history(clean_db):
    card = await LearningService(MockWebullAdapter()).build_scorecard()
    assert card["overall"] == {"sample_size": 0}
    assert card["calibration"] == []
    assert "opportunities_not_taken" not in card

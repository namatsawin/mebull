import pytest
from sqlalchemy import func, select

from apm.db import session_scope
from apm.db.models import Decision as DecisionRow
from apm.orchestrator.app import TradingApp

pytestmark = pytest.mark.asyncio


async def _decision_count() -> int:
    async with session_scope() as s:
        return await s.scalar(select(func.count()).select_from(DecisionRow))


async def test_run_once_makes_one_free_decision(clean_db):
    """The AI decides freely each cycle — no gate. One run_once => one journaled decision."""
    app = TradingApp()
    await app.start()
    try:
        assert await _decision_count() == 0  # startup does not decide
        decision = await app.run_once("PERIODIC")
        assert decision is not None
        assert await _decision_count() == 1
        # Running again makes another independent decision.
        await app.run_once("PERIODIC")
        assert await _decision_count() == 2
    finally:
        await app.stop()

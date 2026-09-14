import asyncio

import pytest
from sqlalchemy import func, select

from apm.db import session_scope
from apm.db.models import Decision as DecisionRow
from apm.events.detector import Event, EventType
from apm.orchestrator.app import TradingApp

pytestmark = pytest.mark.asyncio


async def _decision_count() -> int:
    async with session_scope() as s:
        return await s.scalar(select(func.count()).select_from(DecisionRow))


async def test_meaningful_event_triggers_journaled_decision(clean_db):
    app = TradingApp()
    await app.start()
    try:
        # Startup reconcile does not create a decision.
        assert await _decision_count() == 0

        queued = await app.submit(Event(type=EventType.MARKET_OPEN_REVIEW))
        assert queued is True
        await asyncio.wait_for(app.wait_idle(), timeout=10)

        assert await _decision_count() == 1
    finally:
        await app.stop()


async def test_non_meaningful_event_is_ignored(clean_db):
    app = TradingApp()
    await app.start()
    try:
        queued = await app.submit(
            Event(type=EventType.LARGE_PRICE_MOVE, symbol="NVDA", payload={"change_pct": 0.5})
        )
        assert queued is False
        await asyncio.wait_for(app.wait_idle(), timeout=5)
        assert await _decision_count() == 0
    finally:
        await app.stop()

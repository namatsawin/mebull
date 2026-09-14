"""Integration test fixtures. Require the Postgres from `docker compose up -d db`.

Skips the whole integration suite automatically if the DB is unreachable, so unit-only
runs (and CI without a DB) stay green.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from apm.db import get_engine, get_sessionmaker, session_scope

_TABLES = [
    "trade_event", "trade", "execution", "order_record", "counterfactual",
    "decision_candidate", "decision", "memory_revision", "memory",
    "position", "portfolio_snapshot", "safety_event", "system_event",
    "system_flag", "portfolio",
]


@pytest_asyncio.fixture
async def clean_db():
    # pytest-asyncio runs each test in a fresh event loop; the cached async engine's
    # pool would still be bound to a previous (closed) loop. Reset so a new engine
    # binds to the current loop.
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    engine = get_engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable for integration tests: {exc}")

    async with session_scope() as s:
        for t in _TABLES:
            await s.execute(text(f"TRUNCATE TABLE {t} CASCADE"))

    # Seed the persistent portfolio identity most tests rely on (FK target).
    from apm.config import get_settings
    from apm.db.models import Portfolio

    async with session_scope() as s:
        s.add(
            Portfolio(
                id=get_settings().portfolio_id,
                owner="test",
                broker="Webull",
                execution_mode="MOCK",
            )
        )
    yield

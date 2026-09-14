"""Test fixtures. Require the Postgres from `docker compose up -d db`.

Tests run against a DEDICATED database (``apm_test``) so they never truncate the live app's
data — the live orchestrator writes to ``apm`` on the same server. Skips the integration
suite automatically if the DB is unreachable, so unit-only runs stay green.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import text

import apm.db.models  # noqa: F401 - register models on Base.metadata
from apm.config import get_settings
from apm.db import get_engine, get_sessionmaker, session_scope
from apm.db.base import Base

_TABLES = [
    "trade_event", "trade", "execution", "order_record", "counterfactual",
    "decision_candidate", "decision", "memory_revision", "memory",
    "position", "portfolio_snapshot", "safety_event", "system_event",
    "system_flag", "portfolio",
]


def _test_db_url() -> str:
    base = os.environ.get("APM_DATABASE_URL", "postgresql+asyncpg://apm:apm@localhost:5432/apm")
    return base.rsplit("/", 1)[0] + "/apm_test"


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """Force a clean, safe MOCK test profile AND a dedicated test database, so a developer's
    real `.env` (SANDBOX/REAL creds, Anthropic key) and the live app's data are never touched."""
    monkeypatch.setenv("APM_EXECUTION_MODE", "MOCK")
    monkeypatch.setenv("APM_CLAUDE_PROVIDER", "mock")
    monkeypatch.setenv("APM_TRADING_ENABLED", "false")
    monkeypatch.setenv("APM_MARKET_HOURS_ONLY", "false")
    monkeypatch.setenv("APM_DATABASE_URL", _test_db_url())

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


@pytest_asyncio.fixture
async def clean_db():
    # pytest-asyncio runs each test in a fresh event loop; the cached async engine's pool
    # would still be bound to a previous (closed) loop. Reset so a new engine binds now.
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    engine = get_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            # Ensure the schema exists on the dedicated test DB (no migrations needed here).
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres (apm_test) not reachable for integration tests: {exc}")

    async with session_scope() as s:
        for t in _TABLES:
            await s.execute(text(f"TRUNCATE TABLE {t} CASCADE"))

    # Seed the persistent portfolio identity most tests rely on (FK target).
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

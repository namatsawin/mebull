"""Safety Guard failure tests (spec §65) — every unsafe condition must block."""

import datetime as dt

import pytest
from sqlalchemy import func, select

from apm.config import ExecutionMode, Settings
from apm.db import session_scope
from apm.db.models import SafetyEvent
from apm.domain import OrderRequest, OrderType, Side
from apm.safety.guard import AuthorizationContext, SafetyGuard, SafetyViolation
from apm.safety.killswitch import KillSwitch

pytestmark = pytest.mark.asyncio

NOW = dt.datetime(2026, 1, 2, 15, 0, tzinfo=dt.UTC)


def _ctx(**over):
    base = dict(
        now=NOW,
        buying_power=100_000.0,
        existing_client_order_ids=set(),
        last_reconciled_at=NOW - dt.timedelta(seconds=10),
        reconciled_ok=True,
        recent_order_count=0,
        market_data_ok=True,
        database_ok=True,
    )
    base.update(over)
    return AuthorizationContext(**base)


def _order(**over):
    base = dict(
        client_order_id="apm-abc-0", symbol="NVDA", side=Side.BUY,
        quantity=10, order_type=OrderType.LIMIT, limit_price=100.0,
    )
    base.update(over)
    return OrderRequest(**base)


def _guard(mode=ExecutionMode.SANDBOX, trading_enabled=True):
    settings = Settings(execution_mode=mode, trading_enabled=trading_enabled)
    return SafetyGuard(settings=settings)


async def test_valid_order_is_allowed(clean_db):
    auth = await _guard().authorize(_order(), _ctx())
    assert auth.allowed is True


async def test_duplicate_order_blocked(clean_db):
    guard = _guard()
    ctx = _ctx(existing_client_order_ids={"apm-abc-0"})
    auth = await guard.authorize(_order(client_order_id="apm-abc-0"), ctx)
    assert not auth.allowed
    assert auth.violation is SafetyViolation.DUPLICATE_ORDER


async def test_stale_state_blocked(clean_db):
    ctx = _ctx(last_reconciled_at=NOW - dt.timedelta(seconds=3600))
    auth = await _guard().authorize(_order(), ctx)
    assert auth.violation is SafetyViolation.STALE_STATE


async def test_no_reconciliation_blocked(clean_db):
    auth = await _guard().authorize(_order(), _ctx(last_reconciled_at=None))
    assert auth.violation is SafetyViolation.STALE_STATE


async def test_broker_mismatch_blocked(clean_db):
    auth = await _guard().authorize(_order(), _ctx(reconciled_ok=False))
    assert auth.violation is SafetyViolation.BROKER_MISMATCH


async def test_database_outage_blocked(clean_db):
    auth = await _guard().authorize(_order(), _ctx(database_ok=False))
    assert auth.violation is SafetyViolation.DATABASE_OUTAGE


async def test_insufficient_buying_power_blocked(clean_db):
    ctx = _ctx(buying_power=500.0)  # 10 * 100 = 1000 > 500
    auth = await _guard().authorize(_order(), ctx)
    assert auth.violation is SafetyViolation.INSUFFICIENT_BUYING_POWER


async def test_malformed_limit_without_price_blocked(clean_db):
    auth = await _guard().authorize(
        _order(order_type=OrderType.LIMIT, limit_price=None), _ctx()
    )
    assert auth.violation is SafetyViolation.MALFORMED_ORDER


async def test_invalid_quantity_blocked(clean_db):
    auth = await _guard().authorize(_order(quantity=1_000_000), _ctx())
    assert auth.violation is SafetyViolation.INVALID_QUANTITY


async def test_runaway_frequency_blocked(clean_db):
    auth = await _guard().authorize(_order(), _ctx(recent_order_count=99))
    assert auth.violation is SafetyViolation.RUNAWAY_FREQUENCY


async def test_real_mode_requires_trading_enabled(clean_db):
    guard = _guard(mode=ExecutionMode.REAL, trading_enabled=False)
    auth = await guard.authorize(_order(), _ctx())
    assert auth.violation is SafetyViolation.TRADING_DISABLED


async def test_emergency_stop_blocks_even_sandbox(clean_db):
    await KillSwitch().set_emergency_stop(True, note="drill")
    auth = await _guard().authorize(_order(), _ctx())
    assert auth.violation is SafetyViolation.EMERGENCY_STOP
    await KillSwitch().set_emergency_stop(False)


async def test_every_verdict_is_audited(clean_db):
    await _guard().authorize(_order(), _ctx())  # allowed
    await _guard().authorize(_order(), _ctx(reconciled_ok=False))  # blocked
    async with session_scope() as s:
        n = await s.scalar(select(func.count()).select_from(SafetyEvent))
    assert n == 2

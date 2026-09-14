"""REAL-mode gating (spec §36, §67) — real orders require the flag AND clear kill switch."""

import datetime as dt

import pytest

from apm.config import ExecutionMode, Settings
from apm.domain import OrderRequest, OrderType, Side
from apm.safety.guard import AuthorizationContext, SafetyGuard, SafetyViolation
from apm.safety.killswitch import KillSwitch

pytestmark = pytest.mark.asyncio

NOW = dt.datetime(2026, 1, 2, 15, 0, tzinfo=dt.UTC)


def _ctx():
    return AuthorizationContext(
        now=NOW, buying_power=1_000_000.0,
        last_reconciled_at=NOW - dt.timedelta(seconds=5), reconciled_ok=True,
    )


def _order():
    return OrderRequest(
        client_order_id="apm-real-0", symbol="NVDA", side=Side.BUY,
        quantity=1, order_type=OrderType.LIMIT, limit_price=100.0,
    )


async def test_real_blocked_when_trading_disabled(clean_db):
    guard = SafetyGuard(settings=Settings(execution_mode=ExecutionMode.REAL, trading_enabled=False))
    auth = await guard.authorize(_order(), _ctx())
    assert auth.violation is SafetyViolation.TRADING_DISABLED


async def test_real_allowed_when_enabled_and_clear(clean_db):
    guard = SafetyGuard(settings=Settings(execution_mode=ExecutionMode.REAL, trading_enabled=True))
    auth = await guard.authorize(_order(), _ctx())
    assert auth.allowed is True


async def test_real_blocked_by_emergency_stop_even_when_enabled(clean_db):
    await KillSwitch().set_emergency_stop(True, note="drill")
    guard = SafetyGuard(settings=Settings(execution_mode=ExecutionMode.REAL, trading_enabled=True))
    auth = await guard.authorize(_order(), _ctx())
    assert auth.violation is SafetyViolation.EMERGENCY_STOP
    await KillSwitch().set_emergency_stop(False)

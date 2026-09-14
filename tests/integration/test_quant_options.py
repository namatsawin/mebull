"""Model B (quant options) lifecycle in MOCK — the safety-critical proof before REAL 0DTE.

Verifies: deterministic long-CALL entry through the Safety Guard, and that the EOD flatten
closes an open option position (a held 0DTE must never reach expiry).
"""

from __future__ import annotations

import pytest

from apm.decision.context import DecisionContext, Technicals
from apm.strategy.options import parse_contract_symbol
from apm.strategy.rules import QuantRuleProvider


def test_parse_contract_symbol_both_formats():
    occ = parse_contract_symbol("AMD260914C00495000")
    assert occ["underlying"] == "AMD"
    assert occ["strike"] == 495.0
    assert occ["expiry"] == "2026-09-14"
    assert occ["right"].value == "CALL"

    mock = parse_contract_symbol("AMD 2026-09-14 C 495.0")
    assert mock["underlying"] == "AMD"
    assert mock["strike"] == 495.0
    assert mock["right"].value == "CALL"


@pytest.mark.asyncio
async def test_quant_option_entry(monkeypatch, clean_db):
    monkeypatch.setenv("APM_STRATEGY_MODE", "quant")
    monkeypatch.setenv("APM_OPTIONS_ENABLED", "true")
    from apm.config import get_settings

    get_settings.cache_clear()

    ctx = DecisionContext(
        trigger="T",
        portfolio_id="main-portfolio",
        as_of="now",
        portfolio_state={"portfolio_value": 5000, "positions": []},
        buying_power=5000,
        watchlist=["AMD"],
        technicals=[
            Technicals(
                symbol="AMD", last=100, support=98, resistance=104,
                pct_to_support=2.0, pct_to_resistance=4.0, atr_pct=3.0,
                momentum_5=1.5, trend="up",
            )
        ],
        breadth={"advancers": 4, "decliners": 0, "avg_change_pct": 1.5, "regime": "risk_on"},
        option_chains={
            "AMD": [
                {"right": "CALL", "strike": 100, "expiry": "2026-09-18",
                 "symbol": "AMD 2026-09-18 C 100.0", "bid": 2.0, "ask": 2.1, "mid": 2.05,
                 "cost": 205.0, "delta": 0.52, "iv": 0.45, "open_interest": 1000, "volume": 5000},
            ]
        },
    )
    d = await QuantRuleProvider().analyze(ctx)
    get_settings.cache_clear()
    assert d.decision_type.value == "BUY"
    assert d.instrument_type.value == "CALL_OPTION"
    assert d.symbol == "AMD"
    assert d.option_strike == 100
    assert d.quantity == 1
    assert d.exit_plan.take_profit and d.exit_plan.stop_loss


@pytest.mark.asyncio
async def test_eod_flatten_closes_option(clean_db):
    """A held mock option position must be flattened (sold to close) by flatten_all."""
    from apm.domain import InstrumentType, OrderRequest, OrderType, Side, TimeInForce
    from apm.execution.coordinator import ExecutionCoordinator
    from apm.execution.service import ExecutionService
    from apm.journal.service import JournalService
    from apm.portfolio.service import PortfolioService
    from apm.reconcile.service import ReconciliationService
    from apm.safety.guard import SafetyGuard
    from apm.webull.mock import MockWebullAdapter

    adapter = MockWebullAdapter(volatile=False)
    # Open a long call so there is something to flatten.
    chain = await adapter.get_option_chain("AMD")
    call = next(c for c in chain if c.right.value == "CALL")
    await adapter.place_option_order(
        OrderRequest(
            client_order_id="seed-open", symbol="AMD",
            instrument_type=InstrumentType.CALL_OPTION, side=Side.BUY, quantity=1,
            order_type=OrderType.LIMIT, limit_price=call.ask,
            option_strike=call.strike, option_expiry=call.expiry,
            option_contract_symbol=call.symbol, time_in_force=TimeInForce.DAY,
        )
    )
    _held = await adapter.get_positions()
    assert any(p.instrument_type == InstrumentType.CALL_OPTION for p in _held)

    portfolio = PortfolioService(adapter)
    await portfolio.ensure_portfolio()
    await portfolio.build_state()
    journal = JournalService()
    reconcile = ReconciliationService(adapter, portfolio)
    await reconcile.reconcile(reason="seed")
    guard = SafetyGuard()
    svc = ExecutionService(adapter=adapter, guard=guard, journal=journal,
                           portfolio=portfolio, reconcile=reconcile)
    coord = ExecutionCoordinator(svc, portfolio, adapter)

    closed = await coord.flatten_all(reason="eod-flatten")
    assert closed >= 1
    opts = [p for p in await adapter.get_positions()
            if p.instrument_type == InstrumentType.CALL_OPTION]
    assert not opts, "option position should be flat after EOD flatten"

import pytest

from apm.domain import OrderRequest, OrderType, Side
from apm.portfolio.service import PortfolioService
from apm.reconcile.service import ReconciliationService
from apm.webull.mock import MockWebullAdapter

pytestmark = pytest.mark.asyncio


async def test_build_state_persists_snapshot_and_positions(clean_db):
    adapter = MockWebullAdapter(starting_cash=100_000)
    adapter.set_quote("NVDA", 100.0)
    await adapter.place_order(
        OrderRequest(client_order_id="c1", symbol="NVDA", side=Side.BUY,
                     quantity=10, order_type=OrderType.MARKET)
    )
    svc = PortfolioService(adapter)
    await svc.ensure_portfolio()

    state = await svc.build_state()
    assert state.state_version == 1
    assert state.cash == pytest.approx(99_000)
    assert state.long_exposure == pytest.approx(1000)
    assert state.gross_exposure == pytest.approx(1000)

    # version increments on the next snapshot
    state2 = await svc.build_state()
    assert state2.state_version == 2

    persisted = {p.symbol: p for p in await svc.latest_persisted_positions()}
    assert persisted["NVDA"].quantity == 10


async def test_reconcile_detects_and_corrects_drift(clean_db):
    adapter = MockWebullAdapter(starting_cash=50_000)
    adapter.set_quote("AMD", 200.0)
    svc = PortfolioService(adapter)
    await svc.ensure_portfolio()
    await svc.build_state()  # local: no positions

    recon = ReconciliationService(adapter, svc)

    # No drift yet.
    r0 = await recon.reconcile(reason="startup")
    assert r0.ok is True

    # Introduce broker-side change the local state hasn't seen.
    await adapter.place_order(
        OrderRequest(client_order_id="c2", symbol="AMD", side=Side.BUY,
                     quantity=5, order_type=OrderType.MARKET)
    )
    r1 = await recon.reconcile(reason="post-fill")
    assert r1.ok is False
    assert any(d.symbol == "AMD" and d.broker_qty == 5 for d in r1.position_drifts)

    # After reconciliation, local matches broker -> next reconcile is clean.
    r2 = await recon.reconcile(reason="verify")
    assert r2.ok is True

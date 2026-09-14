import pytest

from apm.domain import OrderRequest, OrderType, Side
from apm.webull.adapter import WebullAdapter
from apm.webull.mock import MockWebullAdapter


def _buy(symbol="NVDA", qty=10, price=None, otype=OrderType.MARKET):
    return OrderRequest(
        client_order_id=f"coid-{symbol}-{qty}-{otype}",
        symbol=symbol,
        side=Side.BUY,
        quantity=qty,
        order_type=otype,
        limit_price=price,
    )


def test_mock_satisfies_protocol():
    assert isinstance(MockWebullAdapter(), WebullAdapter)


async def test_market_buy_fills_and_updates_cash_and_position():
    a = MockWebullAdapter(starting_cash=100_000)
    a.set_quote("NVDA", 100.0)
    order = await a.place_order(_buy("NVDA", 10))
    assert order.status.value == "FILLED"
    assert order.avg_fill_price == 100.0

    positions = {p.symbol: p for p in await a.get_positions()}
    assert positions["NVDA"].quantity == 10
    bal = await a.get_account_balance()
    assert bal.cash == pytest.approx(100_000 - 1000)


async def test_limit_buy_rests_until_price_crosses():
    a = MockWebullAdapter()
    a.set_quote("AMD", 200.0)
    order = await a.place_order(_buy("AMD", 5, price=150.0, otype=OrderType.LIMIT))
    assert order.status.value == "SUBMITTED"  # 200 > 150, not marketable
    assert order in await a.get_open_orders()

    a.set_quote("AMD", 149.0)  # price crosses the limit -> fills
    refreshed = await a.get_order(order.client_order_id)
    assert refreshed.status.value == "FILLED"
    assert refreshed.avg_fill_price == 150.0


async def test_place_order_is_idempotent_by_client_order_id():
    a = MockWebullAdapter()
    a.set_quote("SPY", 400.0)
    req = _buy("SPY", 2)
    first = await a.place_order(req)
    second = await a.place_order(req)  # same client_order_id
    assert first.broker_order_id == second.broker_order_id
    assert len(await a.get_open_orders()) == 0  # both filled, only one order exists


async def test_cancel_open_order():
    a = MockWebullAdapter()
    a.set_quote("TSLA", 300.0)
    order = await a.place_order(_buy("TSLA", 1, price=100.0, otype=OrderType.LIMIT))
    cancelled = await a.cancel_order(order.client_order_id)
    assert cancelled.status.value == "CANCELLED"


async def test_option_chain_and_order():
    from apm.domain import InstrumentType, OptionRight
    a = MockWebullAdapter(starting_cash=100_000)
    a.set_quote("SPY", 760.0)
    chain = await a.get_option_chain("SPY", right=OptionRight.CALL)
    assert chain and all(c.right is OptionRight.CALL for c in chain)
    assert all(c.underlying == "SPY" and c.expiry for c in chain)
    assert chain[0].contract_cost is not None  # priced

    c = chain[0]
    order = await a.place_option_order(
        OrderRequest(
            client_order_id="opt-1", symbol="SPY",
            instrument_type=InstrumentType.CALL_OPTION, side=Side.BUY, quantity=1,
            order_type=OrderType.LIMIT, limit_price=c.mid,
            option_strike=c.strike, option_expiry=c.expiry,
        )
    )
    assert order.status.value == "FILLED"
    assert order.avg_fill_price == c.mid


async def test_historical_bars_are_deterministic():
    a = MockWebullAdapter()
    b1 = await a.get_historical_bars("NVDA", count=50)
    b2 = await a.get_historical_bars("NVDA", count=50)
    assert len(b1) == 50
    assert [x.close for x in b1] == [x.close for x in b2]

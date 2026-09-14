"""MockWebullAdapter — deterministic in-memory broker simulator.

Powers MOCK execution mode and the test suite: a full account with cash, positions,
quotes, synthetic historical bars, and an order engine that fills MARKET orders at the
quote and LIMIT orders when the price crosses. Deterministic (seeded by symbol) so tests
and counterfactual math are reproducible.

Idempotency (spec §33): placing an order whose ``client_order_id`` already exists returns
the existing order rather than creating a duplicate — mirroring safe broker behavior.
"""

from __future__ import annotations

import datetime as dt
import hashlib

from apm.domain import (
    AccountBalance,
    Bar,
    BrokerOrder,
    OptionContract,
    OptionRight,
    OrderPreview,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Quote,
    Side,
)


def _seed_price(symbol: str) -> float:
    """Stable pseudo-price in [20, 520) derived from the symbol."""
    h = int(hashlib.sha256(symbol.encode()).hexdigest(), 16)
    return 20.0 + (h % 50000) / 100.0


class MockWebullAdapter:
    def __init__(
        self,
        *,
        account_id: str = "MOCK-ACCOUNT",
        starting_cash: float = 100_000.0,
        now: dt.datetime | None = None,
    ) -> None:
        self.account_id = account_id
        self._cash = starting_cash
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, BrokerOrder] = {}
        self._prices: dict[str, float] = {}
        # Default to real UTC now so freshness/staleness checks behave against wall-clock.
        self._now = now or dt.datetime.now(dt.UTC)

    # --- test/sim controls ---------------------------------------------------
    def set_quote(self, symbol: str, price: float) -> None:
        self._prices[symbol.upper()] = price
        self._maybe_fill_open_orders(symbol.upper())

    def _price(self, symbol: str) -> float:
        return self._prices.setdefault(symbol.upper(), round(_seed_price(symbol), 2))

    # --- adapter interface ---------------------------------------------------
    async def authenticate(self) -> None:
        return None

    async def get_account_balance(self) -> AccountBalance:
        return AccountBalance(
            account_id=self.account_id,
            cash=round(self._cash, 2),
            buying_power=round(max(self._cash, 0.0), 2),
            total_value=round(self._cash + self._positions_value(), 2),
            as_of=self._now,
        )

    def _positions_value(self) -> float:
        return sum(p.quantity * self._price(p.symbol) for p in self._positions.values())

    async def get_positions(self) -> list[Position]:
        out = []
        for p in self._positions.values():
            out.append(
                Position(
                    symbol=p.symbol,
                    instrument_type=p.instrument_type,
                    quantity=p.quantity,
                    avg_price=p.avg_price,
                    market_price=self._price(p.symbol),
                )
            )
        return out

    async def get_open_orders(self) -> list[BrokerOrder]:
        return [o for o in self._orders.values() if o.status.is_open]

    async def get_order(self, client_order_id: str) -> BrokerOrder | None:
        return self._orders.get(client_order_id)

    async def get_quote(self, symbol: str) -> Quote:
        px = self._price(symbol)
        return Quote(
            symbol=symbol.upper(),
            price=px,
            bid=round(px * 0.999, 2),
            ask=round(px * 1.001, 2),
            volume=1_000_000,
            as_of=self._now,
        )

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        return [await self.get_quote(s) for s in symbols]

    async def get_historical_bars(
        self, symbol: str, *, timespan: str = "d", count: int = 200
    ) -> list[Bar]:
        """Deterministic synthetic random walk ending near the current price."""
        base = self._price(symbol)
        bars: list[Bar] = []
        h = int(hashlib.sha256(symbol.encode()).hexdigest(), 16)
        price = base * 0.8
        for i in range(count):
            drift = ((h >> (i % 32)) & 0xFF) / 255.0 - 0.48
            price = max(1.0, price * (1 + 0.01 * drift))
            ts = self._now - dt.timedelta(days=count - i)
            o = round(price, 2)
            c = round(price * (1 + 0.002 * drift), 2)
            bars.append(
                Bar(
                    symbol=symbol.upper(),
                    timestamp=ts,
                    open=o,
                    high=round(max(o, c) * 1.005, 2),
                    low=round(min(o, c) * 0.995, 2),
                    close=c,
                    volume=1_000_000 + (h % 500_000),
                )
            )
        return bars

    async def preview_order(self, request: OrderRequest) -> OrderPreview:
        px = request.limit_price or self._price(request.symbol)
        cost = px * request.quantity
        bp = self._cash - cost if request.side == Side.BUY else self._cash + cost
        warnings = []
        if request.side == Side.BUY and cost > self._cash:
            warnings.append("estimated cost exceeds cash")
        return OrderPreview(
            ok=True,
            estimated_cost=round(cost, 2),
            estimated_commission=0.0,
            buying_power_after=round(bp, 2),
            warnings=warnings,
        )

    async def place_order(self, request: OrderRequest) -> BrokerOrder:
        # Idempotency (spec §33): never duplicate an existing client_order_id.
        existing = self._orders.get(request.client_order_id)
        if existing is not None:
            return existing

        order = BrokerOrder(
            client_order_id=request.client_order_id,
            broker_order_id=f"MOCK-{len(self._orders) + 1}",
            symbol=request.symbol.upper(),
            side=request.side,
            quantity=request.quantity,
            status=OrderStatus.SUBMITTED,
            order_type=request.order_type,
            limit_price=request.limit_price,
            updated_at=self._now,
        )
        self._orders[request.client_order_id] = order
        self._try_fill(order)
        return order

    async def cancel_order(self, client_order_id: str) -> BrokerOrder:
        order = self._orders[client_order_id]
        if order.status.is_open:
            order.status = OrderStatus.CANCELLED
            order.updated_at = self._now
        return order

    # --- options (single-leg, deterministic) ---------------------------------
    def _default_expiry(self) -> str:
        return (self._now + dt.timedelta(days=30)).date().isoformat()

    def _option_price(self, underlying_px: float, strike: float, right: OptionRight) -> float:
        intrinsic = max(0.0, underlying_px - strike) if right == OptionRight.CALL else max(
            0.0, strike - underlying_px
        )
        time_value = round(underlying_px * 0.02, 2)  # simple, deterministic
        return round(intrinsic + time_value, 2)

    async def get_option_chain(
        self, underlying: str, *, expiry=None, right=None
    ) -> list[OptionContract]:
        px = self._price(underlying)
        expiry = expiry or self._default_expiry()
        rights = [right] if right else [OptionRight.CALL, OptionRight.PUT]
        step = max(1.0, round(px * 0.05, 0))
        base = round(px / step) * step
        out: list[OptionContract] = []
        for r in rights:
            for k in range(-2, 3):  # 5 strikes around ATM
                strike = round(base + k * step, 2)
                if strike <= 0:
                    continue
                p = self._option_price(px, strike, r)
                out.append(
                    OptionContract(
                        underlying=underlying.upper(), right=r, strike=strike, expiry=expiry,
                        symbol=f"{underlying.upper()} {expiry} {r.value[0]} {strike}",
                        last=p, bid=round(p * 0.98, 2), ask=round(p * 1.02, 2),
                    )
                )
        return out

    async def preview_option_order(self, request: OrderRequest) -> OrderPreview:
        px = request.limit_price or 1.0
        cost = px * request.quantity * 100  # 100x multiplier
        return OrderPreview(ok=True, estimated_cost=round(cost, 2), estimated_commission=0.0,
                            buying_power_after=round(self._cash - cost, 2))

    async def place_option_order(self, request: OrderRequest) -> BrokerOrder:
        existing = self._orders.get(request.client_order_id)
        if existing is not None:
            return existing
        fill_px = request.limit_price or 1.0
        order = BrokerOrder(
            client_order_id=request.client_order_id,
            broker_order_id=f"MOCK-OPT-{len(self._orders) + 1}",
            symbol=request.option_contract_symbol
            or f"{request.symbol} {request.option_expiry} {request.instrument_type.value}",
            side=request.side, quantity=request.quantity,
            filled_quantity=request.quantity, avg_fill_price=fill_px,
            status=OrderStatus.FILLED, order_type=request.order_type,
            limit_price=request.limit_price, updated_at=self._now,
        )
        self._orders[request.client_order_id] = order
        signed = 1 if request.side == Side.BUY else -1
        self._cash -= signed * fill_px * request.quantity * 100
        return order

    # --- fill engine ---------------------------------------------------------
    def _maybe_fill_open_orders(self, symbol: str) -> None:
        for o in self._orders.values():
            if o.symbol == symbol and o.status.is_open:
                self._try_fill(o)

    def _try_fill(self, order: BrokerOrder) -> None:
        px = self._price(order.symbol)
        fill = False
        if order.order_type == OrderType.MARKET:
            fill = True
        elif order.order_type == OrderType.LIMIT and order.limit_price is not None:
            if order.side == Side.BUY and px <= order.limit_price:
                fill = True
            elif order.side in (Side.SELL, Side.SHORT) and px >= order.limit_price:
                fill = True
        if not fill:
            return

        fill_px = order.limit_price if order.order_type == OrderType.LIMIT else px
        assert fill_px is not None
        self._apply_fill(order, fill_px)

    def _apply_fill(self, order: BrokerOrder, fill_px: float) -> None:
        order.filled_quantity = order.quantity
        order.avg_fill_price = fill_px
        order.status = OrderStatus.FILLED
        order.updated_at = self._now

        signed = order.quantity if order.side == Side.BUY else -order.quantity
        self._cash -= signed * fill_px

        pos = self._positions.get(order.symbol)
        if pos is None:
            if signed != 0:
                self._positions[order.symbol] = Position(
                    symbol=order.symbol, quantity=signed, avg_price=fill_px
                )
            return
        new_qty = pos.quantity + signed
        if new_qty == 0:
            del self._positions[order.symbol]
        elif (pos.quantity > 0) == (signed > 0):  # adding to same side -> avg
            pos.avg_price = (pos.avg_price * pos.quantity + fill_px * signed) / new_qty
            pos.quantity = new_qty
        else:  # reducing / flipping -> keep avg unless flipped
            if abs(signed) > abs(pos.quantity):
                pos.avg_price = fill_px
            pos.quantity = new_qty

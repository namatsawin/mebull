"""Shared domain models used across every layer.

Broker-agnostic representations of accounts, positions, quotes, bars, and orders.
The Webull adapter maps these to/from the SDK; the rest of the system speaks only
these types (spec §57: adapter hides broker specifics).

Money/prices use ``float`` internally for ergonomic P&L/metrics math; values are
formatted to the broker's tick/precision at the execution boundary (spec §30).
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field


# --- Enums (mirrors of the Webull SDK vocabulary) ---------------------------
class InstrumentType(StrEnum):
    STOCK = "STOCK"
    ETF = "ETF"
    CALL_OPTION = "CALL_OPTION"
    PUT_OPTION = "PUT_OPTION"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    SHORT = "SHORT"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    STOP_LOSS_LIMIT = "STOP_LOSS_LIMIT"


class TimeInForce(StrEnum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"


class OrderStatus(StrEnum):
    """Broker order lifecycle (spec §18). PENDING = locally created, not yet acked."""

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL_FILLED = "PARTIAL_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.FAILED,
            OrderStatus.EXPIRED,
        }

    @property
    def is_open(self) -> bool:
        return self in {OrderStatus.SUBMITTED, OrderStatus.PARTIAL_FILLED}


# --- Account / positions ----------------------------------------------------
class AccountBalance(BaseModel):
    account_id: str
    cash: float
    buying_power: float
    total_value: float
    currency: str = "USD"
    as_of: dt.datetime


class Position(BaseModel):
    symbol: str
    instrument_type: InstrumentType = InstrumentType.STOCK
    quantity: float  # negative = short
    avg_price: float
    market_price: float | None = None

    @property
    def market_value(self) -> float:
        px = self.market_price if self.market_price is not None else self.avg_price
        return self.quantity * px

    @property
    def unrealized_pnl(self) -> float:
        if self.market_price is None:
            return 0.0
        return (self.market_price - self.avg_price) * self.quantity


# --- Market data ------------------------------------------------------------
class Quote(BaseModel):
    symbol: str
    price: float
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    as_of: dt.datetime


class Bar(BaseModel):
    symbol: str
    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class OptionRight(StrEnum):
    CALL = "CALL"
    PUT = "PUT"


class OptionContract(BaseModel):
    """One option contract (single leg). ``expiry`` is YYYY-MM-DD."""

    underlying: str
    right: OptionRight
    strike: float
    expiry: str
    symbol: str | None = None        # broker/OCC option symbol
    instrument_id: str | None = None
    last: float | None = None
    bid: float | None = None
    ask: float | None = None

    @property
    def mid(self) -> float | None:
        if self.bid is not None and self.ask is not None:
            return round((self.bid + self.ask) / 2, 4)
        return self.last

    @property
    def contract_cost(self) -> float | None:
        """Cash to buy one contract (price x100 multiplier)."""
        px = self.mid
        return round(px * 100, 2) if px is not None else None


# --- Orders -----------------------------------------------------------------
class OrderRequest(BaseModel):
    """A request to place one order leg. Broker-agnostic.

    ``client_order_id`` is the idempotency key (spec §33), deterministically derived
    from the originating decision. The Webull SDK accepts it directly and uses it as
    the handle for cancel/detail lookups.
    """

    client_order_id: str
    symbol: str
    instrument_type: InstrumentType = InstrumentType.STOCK
    side: Side
    quantity: float
    order_type: OrderType = OrderType.LIMIT
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    decision_id: str | None = None
    extended_hours: bool = False
    # Option legs (single-leg). Set when instrument_type is CALL_OPTION/PUT_OPTION;
    # ``symbol`` then holds the underlying. Options are LIMIT-only, BUY/SELL only (spec/Webull).
    option_strike: float | None = None
    option_expiry: str | None = None  # YYYY-MM-DD
    option_contract_symbol: str | None = None
    option_strategy: str = "SINGLE"

    @property
    def is_option(self) -> bool:
        return self.instrument_type in (InstrumentType.CALL_OPTION, InstrumentType.PUT_OPTION)


class OrderPreview(BaseModel):
    """Broker cost/impact estimate before submission (spec §30)."""

    ok: bool
    estimated_cost: float | None = None
    estimated_commission: float | None = None
    buying_power_after: float | None = None
    warnings: list[str] = Field(default_factory=list)
    raw: dict = Field(default_factory=dict)


class BrokerOrder(BaseModel):
    """State of an order as known to the broker (source of truth — spec §31)."""

    client_order_id: str
    broker_order_id: str | None = None
    symbol: str
    side: Side
    quantity: float
    filled_quantity: float = 0.0
    avg_fill_price: float | None = None
    status: OrderStatus
    order_type: OrderType = OrderType.LIMIT
    limit_price: float | None = None
    updated_at: dt.datetime | None = None
    raw: dict = Field(default_factory=dict)

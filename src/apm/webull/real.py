"""RealWebullAdapter — wraps the official webull-openapi-python-sdk (v3 endpoints).

The SDK is synchronous (requests/MQTT); every call is dispatched to a worker thread so
the asyncio orchestrator never blocks.

⚠️ LIVE-VERIFICATION REQUIRED (Phase 0 / M9): the SDK method *selection* and auth here
match the installed SDK (v3.0.0), but the exact JSON field names in responses and in the
``new_orders`` body vary by region/version. Every parse below is defensive and preserves
the raw payload in ``.raw``. Before enabling REAL (spec §67, docs/PHASE0_WEBULL_CHECKLIST),
confirm field mappings against a sandbox response and adjust ``_first(...)`` key lists.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

from apm.domain import (
    AccountBalance,
    Bar,
    BrokerOrder,
    OrderPreview,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Quote,
    Side,
)
from apm.observability import get_logger

log = get_logger("webull.real")

_STATUS_MAP = {
    "SUBMITTED": OrderStatus.SUBMITTED,
    "PENDING": OrderStatus.PENDING,
    "PARTIAL_FILLED": OrderStatus.PARTIAL_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "CANCELED": OrderStatus.CANCELLED,
    "FAILED": OrderStatus.FAILED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
}


def _json(response: Any) -> Any:
    """Normalize an SDK response into a dict/list (handles Response or parsed body)."""
    if response is None:
        return {}
    if hasattr(response, "json"):
        try:
            return response.json()
        except Exception:  # noqa: BLE001
            return getattr(response, "text", {}) or {}
    return response


def _first(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class RealWebullAdapter:
    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        region: str,
        account_id: str | None,
        market_category: str = "US_STOCK",
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        # Region id must be lowercase for the SDK endpoint resolver (e.g. "th", "us").
        self._region = region.lower()
        # Traded-market category (e.g. US_STOCK/US_ETF) — this is the market of the SYMBOLS,
        # not the account region. A Webull TH account trading SPY/QQQ still uses US_STOCK.
        self._market_category = market_category.upper()
        self._account_id = account_id
        self._api = None  # webull ApiClient
        self._account = None  # AccountV2
        self._orders = None  # OrderOperationV3
        self._market = None  # MarketData

    # --- lifecycle -----------------------------------------------------------
    async def authenticate(self) -> None:
        if self._api is not None:
            return
        await asyncio.to_thread(self._build_clients)

    def _build_clients(self) -> None:
        # Imported lazily so the core system runs without the optional SDK installed.
        from webull.core.client import ApiClient
        from webull.core.http.initializer.token.token_manager import TokenManager
        from webull.data.quotes.market_data import MarketData
        from webull.trade.trade.v2.account_info_v2 import AccountV2
        from webull.trade.trade.v3.order_operation_v3 import OrderOperationV3

        self._api = ApiClient(self._app_key, self._app_secret, self._region)
        # Obtain + attach the x-access-token (signed with app_key/secret). Cached to a local
        # file and refreshed automatically by the SDK on subsequent runs.
        TokenManager().init_token(self._api)
        self._account = AccountV2(self._api)
        self._orders = OrderOperationV3(self._api)
        self._market = MarketData(self._api)
        if not self._account_id:
            data = _json(self._account.get_account_list())
            accounts = _first(data, "data", "accounts", default=data) or []
            if isinstance(accounts, list) and accounts:
                self._account_id = str(
                    _first(accounts[0], "account_id", "accountId", default="")
                )
        log.info("webull.authenticated", region=self._region, account_bound=bool(self._account_id))

    async def _ensure(self) -> None:
        if self._api is None:
            await self.authenticate()

    @property
    def _category(self) -> str:
        return self._market_category

    # --- reads ---------------------------------------------------------------
    async def get_account_balance(self) -> AccountBalance:
        await self._ensure()
        data = _json(await asyncio.to_thread(self._account.get_account_balance, self._account_id))
        body = _first(data, "data", default=data)
        return AccountBalance(
            account_id=self._account_id or "",
            cash=_f(_first(body, "cash_balance", "cashBalance", "cash")),
            buying_power=_f(_first(body, "buying_power", "buyingPower", "day_buying_power")),
            total_value=_f(_first(body, "net_liquidation", "total_asset", "totalAsset")),
            currency=_first(body, "currency", default="USD"),
            as_of=dt.datetime.now(dt.UTC),
        )

    async def get_positions(self) -> list[Position]:
        await self._ensure()
        data = _json(await asyncio.to_thread(self._account.get_account_position, self._account_id))
        rows = _first(data, "data", "positions", default=[]) or []
        out: list[Position] = []
        for r in rows:
            qty = _f(_first(r, "quantity", "position", "qty"))
            if qty == 0:
                continue
            out.append(
                Position(
                    symbol=str(_first(r, "symbol", "ticker", default="")).upper(),
                    quantity=qty,
                    avg_price=_f(_first(r, "cost_price", "avg_price", "costPrice")),
                    market_price=_first(r, "last_price", "market_price", "lastPrice"),
                )
            )
        return out

    async def get_open_orders(self) -> list[BrokerOrder]:
        await self._ensure()
        data = _json(await asyncio.to_thread(self._orders.get_order_open, self._account_id))
        rows = _first(data, "data", "orders", default=[]) or []
        return [self._parse_order(r) for r in rows]

    async def get_order(self, client_order_id: str) -> BrokerOrder | None:
        await self._ensure()
        data = _json(
            await asyncio.to_thread(
                self._orders.get_order_detail, self._account_id, client_order_id
            )
        )
        body = _first(data, "data", default=data)
        if not body:
            return None
        return self._parse_order(body)

    def _parse_order(self, r: dict) -> BrokerOrder:
        status_raw = str(_first(r, "order_status", "status", default="SUBMITTED")).upper()
        return BrokerOrder(
            client_order_id=str(_first(r, "client_order_id", "clientOrderId", default="")),
            broker_order_id=_first(r, "order_id", "orderId"),
            symbol=str(_first(r, "symbol", "ticker", default="")).upper(),
            side=Side(str(_first(r, "side", default="BUY")).upper().replace("SHORT", "SHORT")),
            quantity=_f(_first(r, "quantity", "qty", "total_quantity")),
            filled_quantity=_f(_first(r, "filled_quantity", "filledQuantity", "filled_qty")),
            avg_fill_price=_first(r, "avg_fill_price", "avgFillPrice", "filled_price"),
            status=_STATUS_MAP.get(status_raw, OrderStatus.SUBMITTED),
            order_type=OrderType(str(_first(r, "order_type", default="LIMIT")).upper()),
            limit_price=_first(r, "limit_price", "limitPrice"),
            updated_at=dt.datetime.now(dt.UTC),
            raw=r if isinstance(r, dict) else {},
        )

    # --- market data ---------------------------------------------------------
    async def get_quote(self, symbol: str) -> Quote:
        quotes = await self.get_quotes([symbol])
        if quotes:
            return quotes[0]
        raise RuntimeError(f"no quote for {symbol}")

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        await self._ensure()
        data = _json(
            await asyncio.to_thread(self._market.get_snapshot, ",".join(symbols), self._category)
        )
        rows = _first(data, "data", default=data) or []
        rows = rows if isinstance(rows, list) else [rows]
        out: list[Quote] = []
        for r in rows:
            out.append(
                Quote(
                    symbol=str(_first(r, "symbol", "ticker", default="")).upper(),
                    price=_f(_first(r, "last_price", "price", "close", "lastPrice")),
                    bid=_first(r, "bid_price", "bid"),
                    ask=_first(r, "ask_price", "ask"),
                    volume=_first(r, "volume"),
                    as_of=dt.datetime.now(dt.UTC),
                )
            )
        return out

    async def get_historical_bars(
        self, symbol: str, *, timespan: str = "d", count: int = 200
    ) -> list[Bar]:
        await self._ensure()
        data = _json(
            await asyncio.to_thread(
                self._market.get_history_bar, symbol, self._category, timespan, str(count)
            )
        )
        rows = _first(data, "data", "bars", default=[]) or []
        out: list[Bar] = []
        for r in rows:
            ts_raw = _first(r, "timestamp", "trade_time", "time", default=0)
            try:
                ts = dt.datetime.fromtimestamp(int(ts_raw) / 1000, tz=dt.UTC)
            except (ValueError, TypeError, OSError):
                ts = dt.datetime.now(dt.UTC)
            out.append(
                Bar(
                    symbol=symbol.upper(),
                    timestamp=ts,
                    open=_f(_first(r, "open")),
                    high=_f(_first(r, "high")),
                    low=_f(_first(r, "low")),
                    close=_f(_first(r, "close")),
                    volume=_f(_first(r, "volume")),
                )
            )
        return out

    # --- writes --------------------------------------------------------------
    def _order_payload(self, req: OrderRequest) -> dict:
        """Build the v3 ``new_orders`` leg. Field names per Webull v3 trading API.

        VERIFY against sandbox before REAL (docs/PHASE0_WEBULL_CHECKLIST).
        """
        itype = req.instrument_type.value
        instrument = "OPTION" if "OPTION" in itype else itype
        payload: dict[str, Any] = {
            "client_order_id": req.client_order_id,
            "symbol": req.symbol.upper(),
            "instrument_type": instrument,
            "market": self._market_category.split("_")[0],  # e.g. "US" from "US_STOCK"
            "side": req.side.value,
            "order_type": req.order_type.value,
            "quantity": str(req.quantity),
            "time_in_force": req.time_in_force.value,
            "extended_hours_trading": req.extended_hours,
        }
        if req.limit_price is not None:
            payload["limit_price"] = str(req.limit_price)
        if req.stop_price is not None:
            payload["stop_price"] = str(req.stop_price)
        return payload

    async def preview_order(self, request: OrderRequest) -> OrderPreview:
        await self._ensure()
        data = _json(
            await asyncio.to_thread(
                self._orders.preview_order, self._account_id, [self._order_payload(request)]
            )
        )
        body = _first(data, "data", default=data) or {}
        return OrderPreview(
            ok=True,
            estimated_cost=_first(body, "estimated_cost", "cost"),
            estimated_commission=_first(body, "commission", "estimated_commission"),
            buying_power_after=_first(body, "buying_power_after", "remaining_buying_power"),
            raw=body if isinstance(body, dict) else {},
        )

    async def place_order(self, request: OrderRequest) -> BrokerOrder:
        await self._ensure()
        data = _json(
            await asyncio.to_thread(
                self._orders.place_order, self._account_id, [self._order_payload(request)]
            )
        )
        body = _first(data, "data", default=data) or {}
        row = body[0] if isinstance(body, list) and body else body
        if not isinstance(row, dict):
            row = {}
        # Merge back the request fields so we always return a coherent order.
        row.setdefault("client_order_id", request.client_order_id)
        row.setdefault("symbol", request.symbol)
        row.setdefault("side", request.side.value)
        row.setdefault("quantity", request.quantity)
        row.setdefault("order_type", request.order_type.value)
        row.setdefault("limit_price", request.limit_price)
        return self._parse_order(row)

    async def cancel_order(self, client_order_id: str) -> BrokerOrder:
        await self._ensure()
        await asyncio.to_thread(self._orders.cancel_order, self._account_id, client_order_id)
        order = await self.get_order(client_order_id)
        if order is None:
            return BrokerOrder(
                client_order_id=client_order_id,
                symbol="",
                side=Side.BUY,
                quantity=0,
                status=OrderStatus.CANCELLED,
            )
        return order

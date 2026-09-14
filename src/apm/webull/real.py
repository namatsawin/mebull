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
import time
from typing import Any

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
from apm.observability import get_logger

log = get_logger("webull.real")

# Market prefix (from the category, e.g. "US_STOCK" -> "US") to its trading currency.
_MARKET_CURRENCY = {"US": "USD", "HK": "HKD", "CN": "CNH", "TH": "THB"}

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


def _fopt(v: Any) -> float | None:
    """Float or None (keeps optional greeks/IV empty rather than 0.0 when absent)."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _parse_contract(underlying: str, r: dict) -> OptionContract | None:
    """Parse one contract-metadata row from the option-contracts endpoint."""
    try:
        rt = str(_first(r, "option_type", "direction", default="CALL")).upper()
        strike = _fopt(_first(r, "strike_price", "strike"))
        expiry = str(_first(r, "expiration_date", "expire_date", "expiry", default=""))
        if not strike or not expiry:
            return None
        return OptionContract(
            underlying=underlying,
            right=OptionRight.PUT if rt.startswith("P") else OptionRight.CALL,
            strike=strike,
            expiry=expiry,
            symbol=_first(r, "symbol", "option_symbol"),
            instrument_id=_first(r, "instrument_id"),
        )
    except Exception:  # noqa: BLE001 - skip malformed rows
        return None


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
        min_request_interval: float = 1.0,
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
        # Throttle: the Webull API rate-limits bursts (429). Serialize all calls and keep a
        # minimum gap between them so a decision cycle's several reads don't trip the limit.
        self._lock = asyncio.Lock()
        self._min_interval = min_request_interval
        self._last_call = 0.0

    async def _dispatch(self, fn, *args):
        """Run a (sync) SDK call off-thread, serialized + spaced to respect rate limits."""
        async with self._lock:
            gap = self._min_interval - (time.monotonic() - self._last_call)
            if gap > 0:
                await asyncio.sleep(gap)
            try:
                return await asyncio.to_thread(fn, *args)
            finally:
                self._last_call = time.monotonic()

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

        self._api = ApiClient(
            self._app_key, self._app_secret, self._region,
            auto_retry=True, max_retry_num=3,
        )
        # Reuse a previously verified access token if one is cached locally; only run the
        # interactive create+verify flow (blocks until the token is approved in the Webull
        # app) when there is no cached token. This avoids minting a new PENDING token on
        # every run. Set up the first token with `apm-webull-token` (see docs/RUNBOOK).
        tm = TokenManager()
        cached = tm.load_token_from_local()
        token = getattr(cached, "token", None) if cached else None
        if token:
            self._api.set_token(token)
            log.info("webull.token.cached")
        else:
            tm.init_token(self._api)
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
        data = _json(await self._dispatch(self._account.get_account_balance, self._account_id))
        body = _first(data, "data", default=data)
        # Webull returns a base-currency total plus a per-currency breakdown
        # (account_currency_assets). We trade in the market's currency (US -> USD), so use
        # that sub-account's buying power; fall back to the base-currency totals.
        currency = _MARKET_CURRENCY.get(self._market_category.split("_")[0], "USD")
        assets = body.get("account_currency_assets") if isinstance(body, dict) else None
        sub = next((a for a in (assets or []) if a.get("currency") == currency), None)
        if sub:
            cash = _f(sub.get("cash_balance"))
            buying_power = _f(sub.get("buying_power"))
            market_value = _f(sub.get("market_value"))
        else:
            cash = _f(_first(body, "total_cash_balance", "cash_balance", "cash"))
            buying_power = _f(_first(body, "buying_power", "day_buying_power")) or cash
            market_value = _f(_first(body, "total_market_value", "net_liquidation"))
            currency = _first(body, "total_asset_currency", "currency", default="USD")
        return AccountBalance(
            account_id=self._account_id or "",
            cash=cash,
            buying_power=buying_power,
            total_value=market_value + cash,
            currency=currency,
            as_of=dt.datetime.now(dt.UTC),
        )

    async def get_positions(self) -> list[Position]:
        await self._ensure()
        data = _json(await self._dispatch(self._account.get_account_position, self._account_id))
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
        data = _json(await self._dispatch(self._orders.get_order_open, self._account_id))
        rows = _first(data, "data", "orders", default=[]) or []
        return [self._parse_order(r) for r in rows]

    async def get_order(self, client_order_id: str) -> BrokerOrder | None:
        await self._ensure()
        data = _json(
            await self._dispatch(
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
            await self._dispatch(self._market.get_snapshot, ",".join(symbols), self._category)
        )
        rows = _first(data, "data", default=data) or []
        rows = rows if isinstance(rows, list) else [rows]
        out: list[Quote] = []
        for r in rows:
            change_ratio = _first(r, "change_ratio")
            change_pct = _f(change_ratio) * 100 if change_ratio is not None else None
            out.append(
                Quote(
                    symbol=str(_first(r, "symbol", "ticker", default="")).upper(),
                    price=_f(_first(r, "last_price", "price", "close", "lastPrice")),
                    bid=_first(r, "bid_price", "bid"),
                    ask=_first(r, "ask_price", "ask"),
                    volume=_first(r, "volume"),
                    change_pct=round(change_pct, 2) if change_pct is not None else None,
                    day_open=_first(r, "open"),
                    day_high=_first(r, "high"),
                    day_low=_first(r, "low"),
                    prev_close=_first(r, "pre_close", "prev_close", "close"),
                    as_of=dt.datetime.now(dt.UTC),
                )
            )
        return out

    async def get_historical_bars(
        self, symbol: str, *, timespan: str = "d", count: int = 200
    ) -> list[Bar]:
        await self._ensure()
        data = _json(
            await self._dispatch(
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
        # Webull v3 trading API expects EQUITY for stocks/ETFs and OPTION for options — NOT the
        # domain names STOCK/ETF (those return 417 "Instrument type invalid"). LIVE-VERIFIED.
        if "OPTION" in itype:
            instrument = "OPTION"
        elif itype in ("STOCK", "ETF"):
            instrument = "EQUITY"
        else:
            instrument = itype
        payload: dict[str, Any] = {
            "client_order_id": req.client_order_id,
            "symbol": req.symbol.upper(),
            "instrument_type": instrument,
            "market": self._market_category.split("_")[0],  # e.g. "US" from "US_STOCK"
            "side": req.side.value,
            "order_type": req.order_type.value,
            "quantity": str(req.quantity),
            "time_in_force": req.time_in_force.value,
            # v3 US equities: which sessions the order is valid in. "N" = regular hours only;
            # "Y" would allow pre/post-market. LIVE-VERIFIED (missing/invalid -> 417).
            "support_trading_session": "Y" if req.extended_hours else "N",
            "entrust_type": "QTY",
        }
        if req.limit_price is not None:
            payload["limit_price"] = str(req.limit_price)
        if req.stop_price is not None:
            payload["stop_price"] = str(req.stop_price)
        return payload

    async def preview_order(self, request: OrderRequest) -> OrderPreview:
        await self._ensure()
        data = _json(
            await self._dispatch(
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
            await self._dispatch(
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
        await self._dispatch(self._orders.cancel_order, self._account_id, client_order_id)
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

    # --- options (single-leg) ------------------------------------------------
    # LIVE-VERIFIED with OPRA: contracts endpoint returns a top-level list of contract metadata;
    # the option snapshot adds bid/ask/greeks/IV/OI. We fetch a near-dated, near-ATM slice and
    # enrich it with a batched snapshot so the AI/rules get everything they need in one shot.
    async def get_option_chain(
        self,
        underlying: str,
        *,
        expiry: str | None = None,
        right: OptionRight | None = None,
        max_dte: int = 7,
        strikes_each_side: int = 6,
    ) -> list[OptionContract]:
        await self._ensure()
        underlying = underlying.upper()

        def _contracts():
            from webull.data.request.get_option_contracts_request import (
                GetOptionContractsRequest,
            )

            req = GetOptionContractsRequest()
            req.set_category("US_OPTION")
            req.set_underlying_symbols(underlying)
            if right is not None:
                req.set_option_type(right.value)
            if expiry:
                req.set_start_date(expiry)
                req.set_end_date(expiry)
            elif hasattr(req, "set_end_date"):
                # Near-dated only (0DTE..max_dte) — keeps the payload small for intraday use.
                req.set_end_date((dt.date.today() + dt.timedelta(days=max_dte)).isoformat())
            return self._api.get_response(req)

        data = _json(await self._dispatch(_contracts))
        rows = data if isinstance(data, list) else _first(data, "data", "contracts", default=[])
        rows = rows or []
        # STANDARD contracts only — drop adjusted/FLEX series (root like "2GOOG"/"4QQQ") whose
        # symbols the option snapshot rejects (417 INVALID_SYMBOL) and which we don't trade.
        rows = [
            r
            for r in rows
            if str(_first(r, "root_symbol", default=underlying)).upper() == underlying
            and str(_first(r, "def_type", default="STANDARD")).upper() == "STANDARD"
        ]

        # Narrow to the nearest expiry and strikes around the money before the snapshot call.
        px = None
        try:
            q = await self.get_quote(underlying)
            px = q.price
        except Exception:  # noqa: BLE001 - underlying quote optional for filtering
            px = None
        contracts = [_parse_contract(underlying, r) for r in rows]
        contracts = [c for c in contracts if c is not None]
        if not contracts:
            return []
        nearest = min({c.expiry for c in contracts if c.expiry})
        chosen_expiry = expiry or nearest
        contracts = [c for c in contracts if c.expiry == chosen_expiry]
        if px is not None:
            contracts.sort(key=lambda c: abs(c.strike - px))
            keep = strikes_each_side * (1 if right else 2)
            contracts = contracts[: max(keep, 1)]

        await self._enrich_option_snapshots(contracts)
        return contracts

    async def _enrich_option_snapshots(self, contracts: list[OptionContract]) -> None:
        """Attach bid/ask/last/greeks/IV/OI from the OPRA option snapshot (batched)."""
        syms = [c.symbol for c in contracts if c.symbol]
        if not syms:
            return

        def _snap():
            from webull.data.request.get_option_snapshot_request import (
                GetOptionSnapshotRequest,
            )

            r = GetOptionSnapshotRequest()
            r.set_symbols(",".join(syms))
            r.set_category("US_OPTION")
            return self._api.get_response(r)

        data = _json(await self._dispatch(_snap))
        rows = data if isinstance(data, list) else (_first(data, "data", default=[]) or [])
        by_sym = {str(_first(r, "symbol", default="")): r for r in rows}
        for c in contracts:
            r = by_sym.get(c.symbol or "")
            if not r:
                continue
            c.bid = _fopt(_first(r, "bid", "bid_price"))
            c.ask = _fopt(_first(r, "ask", "ask_price"))
            c.last = _fopt(_first(r, "price", "last_price", "close"))
            c.delta = _fopt(_first(r, "delta"))
            c.gamma = _fopt(_first(r, "gamma"))
            c.theta = _fopt(_first(r, "theta"))
            c.vega = _fopt(_first(r, "vega"))
            c.iv = _fopt(_first(r, "imp_vol", "implied_volatility"))
            c.open_interest = _fopt(_first(r, "open_interest"))
            c.volume = _fopt(_first(r, "volume"))

    def _option_payload(self, req: OrderRequest) -> dict:
        """v3 single-leg option order. LIVE-VERIFIED shape: flat strategy fields on top +
        one leg keyed by UNDERLYING symbol + option_type/strike/expiry (NOT the OCC symbol),
        with side + position_intent on both. Long-only flow: BUY=open, SELL=close."""
        option_type = "CALL" if req.instrument_type.value == "CALL_OPTION" else "PUT"
        intent = "BUY_TO_OPEN" if req.side.value == "BUY" else "SELL_TO_CLOSE"
        market = self._market_category.split("_")[0]
        leg = {
            "instrument_type": "OPTION",
            "market": market,
            "symbol": req.symbol.upper(),  # underlying
            "option_type": option_type,
            "strike_price": str(req.option_strike),
            "option_expire_date": req.option_expiry,
            "side": req.side.value,
            "position_intent": intent,
            "quantity": str(req.quantity),
            "ratio": "1",
        }
        payload: dict[str, Any] = {
            "client_order_id": req.client_order_id,
            "combo_type": "NORMAL",
            "option_strategy": req.option_strategy or "SINGLE",
            "instrument_type": "OPTION",
            "side": req.side.value,
            "position_intent": intent,
            "order_type": req.order_type.value,
            "quantity": str(req.quantity),
            "support_trading_session": "N",
            "entrust_type": "QTY",
            "time_in_force": req.time_in_force.value,
            "legs": [leg],
        }
        if req.limit_price is not None:
            payload["limit_price"] = str(req.limit_price)
        return payload

    async def preview_option_order(self, request: OrderRequest) -> OrderPreview:
        await self._ensure()
        data = _json(
            await self._dispatch(
                self._orders.preview_order, self._account_id, [self._option_payload(request)]
            )
        )
        body = _first(data, "data", default=data) or {}
        if isinstance(body, list) and body:
            body = body[0]
        return OrderPreview(
            ok=True,
            estimated_cost=_first(body, "estimated_cost", "cost"),
            estimated_commission=_first(body, "estimated_transaction_fee", "commission"),
            raw=body if isinstance(body, dict) else {},
        )

    async def place_option_order(self, request: OrderRequest) -> BrokerOrder:
        await self._ensure()
        data = _json(
            await self._dispatch(
                self._orders.place_order, self._account_id, [self._option_payload(request)]
            )
        )
        body = _first(data, "data", default=data) or {}
        row = body[0] if isinstance(body, list) and body else body
        if not isinstance(row, dict):
            row = {}
        row.setdefault("client_order_id", request.client_order_id)
        row.setdefault("symbol", request.option_contract_symbol or request.symbol)
        row.setdefault("side", request.side.value)
        row.setdefault("quantity", request.quantity)
        row.setdefault("order_type", request.order_type.value)
        row.setdefault("limit_price", request.limit_price)
        return self._parse_order(row)

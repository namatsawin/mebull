"""WebullAdapter Protocol — the single broker seam (spec §57).

All broker access in the system goes through this interface. Implementations:
  - MockWebullAdapter   (apm.webull.mock)  — in-memory, deterministic; MOCK mode & tests
  - RealWebullAdapter   (apm.webull.real)  — official webull-openapi-python-sdk; SANDBOX/REAL

Webull is the source of truth for account/positions/orders (spec §31); read methods
here are authoritative over local state during reconciliation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from apm.domain import (
    AccountBalance,
    Bar,
    BrokerOrder,
    OrderPreview,
    OrderRequest,
    Position,
    Quote,
)


@runtime_checkable
class WebullAdapter(Protocol):
    async def authenticate(self) -> None:
        """Establish/refresh credentials. Idempotent."""
        ...

    # --- Read (source of truth) ---------------------------------------------
    async def get_account_balance(self) -> AccountBalance: ...

    async def get_positions(self) -> list[Position]: ...

    async def get_open_orders(self) -> list[BrokerOrder]: ...

    async def get_order(self, client_order_id: str) -> BrokerOrder | None: ...

    # --- Market data ---------------------------------------------------------
    async def get_quote(self, symbol: str) -> Quote: ...

    async def get_quotes(self, symbols: list[str]) -> list[Quote]: ...

    async def get_historical_bars(
        self, symbol: str, *, timespan: str = "d", count: int = 200
    ) -> list[Bar]: ...

    # --- Write (must be routed through the Safety Guard by callers) ----------
    async def preview_order(self, request: OrderRequest) -> OrderPreview: ...

    async def place_order(self, request: OrderRequest) -> BrokerOrder: ...

    async def cancel_order(self, client_order_id: str) -> BrokerOrder: ...

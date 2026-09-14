"""Live, READ-ONLY verification of the REAL Webull order path (no orders placed).

Confirms: auth, balance, a quote, an EQUITY *preview* (was 417), and option-chain/OPRA access.
Run: uv run python scripts/verify_real_orderpath.py
"""

from __future__ import annotations

import asyncio

from apm.domain import InstrumentType, OrderRequest, OrderType, Side, TimeInForce
from apm.webull import build_adapter


async def main() -> None:
    a = build_adapter()
    await a.authenticate()

    bal = await a.get_account_balance()
    print(f"[balance] account={bal.account_id} cash={bal.cash} bp={bal.buying_power} "
          f"total={bal.total_value}")

    sym = "AMD"
    q = await a.get_quote(sym)
    print(f"[quote] {sym} price={q.price} bid={q.bid} ask={q.ask}")

    # EQUITY preview — a 1-share LIMIT far below market. Preview only: NO order is placed.
    limit = round((q.price or 10.0) * 0.5, 2)
    req = OrderRequest(
        client_order_id="verify-preview-amd-1",
        symbol=sym,
        instrument_type=InstrumentType.STOCK,
        side=Side.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=limit,
        time_in_force=TimeInForce.DAY,
    )
    try:
        p = await a.preview_order(req)
        print(f"[preview EQUITY] OK ok={p.ok} est_cost={p.estimated_cost} "
              f"bp_after={p.buying_power_after} raw={p.raw}")
    except Exception as exc:  # noqa: BLE001
        print(f"[preview EQUITY] FAILED: {type(exc).__name__}: {exc}")

    # OPRA / options access probe.
    try:
        chain = await a.get_option_chain(sym)
        print(f"[option chain] OK contracts={len(chain)} sample={chain[:1]}")
    except Exception as exc:  # noqa: BLE001
        print(f"[option chain] UNAVAILABLE: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())

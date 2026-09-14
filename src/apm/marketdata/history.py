"""Historical bars from a free source (Yahoo) — the time-series Webull's entitlement doesn't
serve (its bars endpoint 404s). DATA-ONLY; never execution.

- ``seed_history`` fetches daily + 5-minute bars (and ^VIX) into ``price_bar`` at startup,
  idempotent (skips bars already stored) and graceful (a fetch failure never blocks the app).
- ``hv20`` / ``volume_z`` / ``latest_vix`` read those bars back for the quant signals.

yfinance is unofficial + delayed (~15m intraday). Fine for the historical/context layer
(HV, VIX, volume baseline); real-time price/greeks still come from Webull.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import math

from sqlalchemy import select

from apm.db import session_scope
from apm.db.models import PriceBar
from apm.observability import get_logger

log = get_logger("history")

# interval -> (yahoo period, max staleness before we refetch)
_PLAN: dict[str, tuple[str, dt.timedelta]] = {
    "1d": ("1y", dt.timedelta(days=2)),
    "5m": ("5d", dt.timedelta(hours=12)),
}
VIX_SYMBOL = "^VIX"


def _fetch_yahoo(symbol: str, interval: str, period: str) -> list[dict]:
    """Blocking Yahoo fetch → list of bar dicts (UTC-naive ts). Runs in a thread."""
    import yfinance as yf  # imported lazily so the core runs without it

    df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
    out: list[dict] = []
    for ts, row in df.iterrows():
        py = ts.to_pydatetime()
        if py.tzinfo is not None:
            py = py.astimezone(dt.UTC).replace(tzinfo=None)
        try:
            out.append(
                {
                    "ts": py,
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row["Volume"]),
                }
            )
        except (ValueError, TypeError, KeyError):
            continue
    return out


async def _latest_ts(symbol: str, interval: str) -> dt.datetime | None:
    async with session_scope() as s:
        return await s.scalar(
            select(PriceBar.ts)
            .where(PriceBar.symbol == symbol, PriceBar.interval == interval)
            .order_by(PriceBar.ts.desc())
            .limit(1)
        )


async def _seed_one(symbol: str, interval: str) -> int:
    period, max_age = _PLAN[interval]
    latest = await _latest_ts(symbol, interval)
    if latest is not None and (dt.datetime.now(dt.UTC).replace(tzinfo=None) - latest) < max_age:
        return 0  # fresh enough
    try:
        bars = await asyncio.to_thread(_fetch_yahoo, symbol, interval, period)
    except Exception as exc:  # noqa: BLE001 - free source may be down/rate-limited
        log.warning("history.fetch_failed", symbol=symbol, interval=interval, error=str(exc))
        return 0
    if not bars:
        return 0
    async with session_scope() as s:
        existing = set(
            (
                await s.scalars(
                    select(PriceBar.ts).where(
                        PriceBar.symbol == symbol, PriceBar.interval == interval
                    )
                )
            ).all()
        )
        added = 0
        for b in bars:
            if b["ts"] in existing:
                continue
            s.add(PriceBar(symbol=symbol, interval=interval, source="yahoo", **b))
            added += 1
    if added:
        log.info("history.seeded", symbol=symbol, interval=interval, bars=added)
    return added


async def seed_history(symbols: list[str]) -> int:
    """Seed daily + 5m bars for each symbol (and ^VIX daily). Idempotent + graceful."""
    total = 0
    targets = [(s.upper(), iv) for s in symbols for iv in ("1d", "5m")]
    targets.append((VIX_SYMBOL, "1d"))
    for sym, iv in targets:
        try:
            total += await _seed_one(sym, iv)
        except Exception as exc:  # noqa: BLE001 - never block startup on data seeding
            log.warning("history.seed_error", symbol=sym, interval=iv, error=str(exc))
    log.info("history.seed_complete", symbols=len(symbols), bars_added=total)
    return total


# --- read-back signals -------------------------------------------------------
async def _closes(symbol: str, interval: str, limit: int) -> list[float]:
    async with session_scope() as s:
        rows = (
            await s.scalars(
                select(PriceBar.close)
                .where(PriceBar.symbol == symbol.upper(), PriceBar.interval == interval)
                .order_by(PriceBar.ts.desc())
                .limit(limit)
            )
        ).all()
    return list(reversed(rows))


async def hv20(symbol: str) -> float | None:
    """20-day annualized historical volatility (%) from daily closes."""
    closes = await _closes(symbol, "1d", 21)
    if len(closes) < 21:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1]]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(252) * 100, 1)


async def volume_z(symbol: str) -> float | None:
    """Z-score of the latest 5m volume vs the prior 20 bars (Model B order-flow trigger)."""
    async with session_scope() as s:
        vols = (
            await s.scalars(
                select(PriceBar.volume)
                .where(PriceBar.symbol == symbol.upper(), PriceBar.interval == "5m")
                .order_by(PriceBar.ts.desc())
                .limit(21)
            )
        ).all()
    if len(vols) < 21:
        return None
    cur = vols[0]
    window = vols[1:21]
    mean = sum(window) / len(window)
    var = sum((v - mean) ** 2 for v in window) / (len(window) - 1)
    std = math.sqrt(var)
    if std <= 0:
        return None
    return round((cur - mean) / std, 2)


async def latest_vix() -> float | None:
    closes = await _closes(VIX_SYMBOL, "1d", 1)
    return round(closes[-1], 2) if closes else None

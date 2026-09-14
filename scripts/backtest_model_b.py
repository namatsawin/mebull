"""Backtest the Model B ENTRY SIGNAL on the underlying (no look-ahead).

We have no free historical option prices, so we validate the *signal* on the stock: when the
Model B trigger fires (5m volume_z >= Z + up-momentum + up-trend), does the underlying move up
over the next K bars more than baseline? If the signal has no edge here, the option version
(which pays spread + theta on top) can't work. Small sample (~5d of 5m bars) → indicative only.

Run: uv run python scripts/backtest_model_b.py
"""

from __future__ import annotations

import asyncio
import statistics as st

from apm.config import get_settings

LOOKBACK_DAYS = 60   # Yahoo max for 5m interval → ~4600 bars/symbol
Z_MIN = 2.0          # volume_z trigger
K_FWD = 6            # forward horizon in 5m bars (30 min)
MOM_LOOKBACK = 3     # bars for up-momentum (15 min)
TREND_LOOKBACK = 6   # bars for up-trend (30 min)
VOL_WIN = 20         # bars for volume mean/std


def _bars(symbol: str) -> list[tuple]:
    """Fetch ~60d of 5m bars straight from Yahoo (bigger sample than the seeded 5d)."""
    import yfinance as yf

    df = yf.Ticker(symbol).history(period=f"{LOOKBACK_DAYS}d", interval="5m", auto_adjust=True)
    return [(ts, float(r["Close"]), float(r["Volume"])) for ts, r in df.iterrows()]


def _zscore(vols: list[float], i: int) -> float | None:
    if i < VOL_WIN:
        return None
    win = vols[i - VOL_WIN:i]
    m = sum(win) / len(win)
    sd = st.pstdev(win)
    return (vols[i] - m) / sd if sd > 0 else None


def _backtest_symbol(bars: list[tuple]) -> dict:
    closes = [b[1] for b in bars]
    vols = [b[2] for b in bars]
    n = len(bars)
    sig_fwd: list[float] = []   # forward returns when signal fires
    base_fwd: list[float] = []  # forward returns for ALL eligible bars (baseline)
    for i in range(VOL_WIN, n - K_FWD):
        fwd = (closes[i + K_FWD] / closes[i] - 1) * 100
        base_fwd.append(fwd)
        z = _zscore(vols, i)
        if z is None or z < Z_MIN:
            continue
        up_mom = closes[i] > closes[i - MOM_LOOKBACK]
        up_trend = closes[i] > closes[i - TREND_LOOKBACK]
        if up_mom and up_trend:
            sig_fwd.append(fwd)
    return {"signals": sig_fwd, "baseline": base_fwd}


def _stats(name: str, fwd: list[float]) -> str:
    if not fwd:
        return f"{name}: n=0"
    avg = sum(fwd) / len(fwd)
    win = sum(1 for x in fwd if x > 0) / len(fwd) * 100
    med = st.median(fwd)
    return f"{name}: n={len(fwd):4d}  avg_fwd={avg:+.3f}%  win={win:4.1f}%  med={med:+.3f}%"


async def main() -> None:
    symbols = get_settings().watchlist_symbols
    all_sig: list[float] = []
    all_base: list[float] = []
    print(f"Model B signal backtest — Z>={Z_MIN}, fwd={K_FWD}x5m (30m), n_sym={len(symbols)}\n")
    for sym in symbols:
        bars = await asyncio.to_thread(_bars, sym)
        if len(bars) < VOL_WIN + K_FWD + 1:
            print(f"{sym:5s} — not enough bars ({len(bars)})")
            continue
        r = _backtest_symbol(bars)
        all_sig += r["signals"]
        all_base += r["baseline"]
        print(f"{sym:5s}  {_stats('signal', r['signals'])}   | {_stats('base', r['baseline'])}")
    print("\n=== AGGREGATE ===")
    print(_stats("SIGNAL  ", all_sig))
    print(_stats("BASELINE", all_base))
    if all_sig and all_base:
        edge = (sum(all_sig) / len(all_sig)) - (sum(all_base) / len(all_base))
        print(f"\nEDGE (signal avg − baseline avg) = {edge:+.3f}% over 30m")
        print(
            f"NOTE: underlying-only, ~{LOOKBACK_DAYS}d 5m sample. Options need a move > "
            "spread(~5%)+theta to profit — so a near-zero underlying edge => negative after costs."
        )


if __name__ == "__main__":
    asyncio.run(main())

"""Research harness: test a PANEL of candidate intraday signals on the underlying (no
look-ahead), with an OPTION-relevant metric.

A long 0DTE call needs the underlying to actually MOVE (to beat spread+theta), so we use a
first-touch barrier: after a signal, does price hit +B% before −B% within K bars? A signal is
promising only if that hit-rate clears the baseline by a meaningful margin.

Free data (Yahoo 60d/5m), offline, no cost. Small-ish sample + multiple-testing => treat any
winner as a hypothesis to confirm, not proof.

Run: uv run python scripts/research_signals.py
"""

from __future__ import annotations

import asyncio
import statistics as st

from apm.config import get_settings

DAYS = 60
BARRIER = 0.8      # +/- % first-touch barrier (≈ a tradeable 0DTE move)
K = 12             # horizon in 5m bars (60 min)
VOL_WIN = 20


def _fetch(symbol: str):
    import yfinance as yf

    df = yf.Ticker(symbol).history(period=f"{DAYS}d", interval="5m", auto_adjust=True)
    o = [float(x) for x in df["Open"]]
    h = [float(x) for x in df["High"]]
    low = [float(x) for x in df["Low"]]
    c = [float(x) for x in df["Close"]]
    v = [float(x) for x in df["Volume"]]
    days = [ts.date() for ts in df.index]
    return o, h, low, c, v, days


def _rsi(c: list[float], i: int, n: int = 14) -> float | None:
    if i < n:
        return None
    gains = losses = 0.0
    for j in range(i - n + 1, i + 1):
        d = c[j] - c[j - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100 - 100 / (1 + rs)


def _volz(v: list[float], i: int) -> float | None:
    if i < VOL_WIN:
        return None
    win = v[i - VOL_WIN:i]
    sd = st.pstdev(win)
    return (v[i] - sum(win) / len(win)) / sd if sd > 0 else None


def _barrier_up(h, low, c, i: int) -> int | None:
    """1 if +BARRIER% is touched before −BARRIER% within K bars, 0 if the down barrier first,
    None if neither within the horizon (indeterminate — excluded)."""
    entry = c[i]
    up, dn = entry * (1 + BARRIER / 100), entry * (1 - BARRIER / 100)
    for j in range(i + 1, min(i + 1 + K, len(c))):
        hit_up = h[j] >= up
        hit_dn = low[j] <= dn
        if hit_up and hit_dn:
            return 1 if (c[j] >= entry) else 0  # both in one bar: approximate by close
        if hit_up:
            return 1
        if hit_dn:
            return 0
    return None


# --- candidate bullish signals (expect an up move) --------------------------
def _signals(o, h, low, c, v, days, i: int) -> list[str]:
    out = []
    z = _volz(v, i)
    # S1 current Model B: volume spike + up momentum + up trend
    if z is not None and z >= 2 and c[i] > c[i - 3] and c[i] > c[i - 6]:
        out.append("S1_momentum_volz")
    # S2 Donchian-20 breakout (new 20-bar high)
    if i >= 20 and c[i] > max(h[i - 20:i]):
        out.append("S2_donchian_breakout")
    # S3 RSI oversold bounce (mean reversion)
    r = _rsi(c, i)
    if r is not None and r < 25:
        out.append("S3_rsi_oversold")
    # S4 opening-range breakout: break first-6-bar high, only in first 2h of a day
    day_start = next((k for k in range(i, -1, -1) if k == 0 or days[k - 1] != days[i]), i)
    or_len = 6
    if day_start + or_len <= i < day_start + 24:
        orh = max(h[day_start:day_start + or_len])
        if c[i] > orh and c[i - 1] <= orh:
            out.append("S4_opening_range")
    # S5 big green candle + volume
    if z is not None and z >= 1.5 and (c[i] - o[i]) / o[i] * 100 >= 0.3:
        out.append("S5_big_green_vol")
    return out


async def main() -> None:
    symbols = get_settings().watchlist_symbols
    from collections import defaultdict

    hits: dict[str, list[int]] = defaultdict(list)
    base: list[int] = []
    print(f"Signal research — barrier ±{BARRIER}% within {K}x5m ({K*5}m), {DAYS}d, "
          f"{len(symbols)} symbols\n")
    for sym in symbols:
        try:
            o, h, low, c, v, days = await asyncio.to_thread(_fetch, sym)
        except Exception as exc:  # noqa: BLE001
            print(f"{sym}: fetch failed {exc}")
            continue
        for i in range(VOL_WIN, len(c) - 1):
            b = _barrier_up(h, low, c, i)
            if b is None:
                continue
            base.append(b)
            for name in _signals(o, h, low, c, v, days, i):
                hits[name].append(b)

    def rate(xs: list[int]) -> float:
        return (sum(xs) / len(xs) * 100) if xs else 0.0

    base_rate = rate(base)
    print(f"BASELINE  n={len(base):6d}  P(+{BARRIER}% first)={base_rate:5.1f}%\n")
    print(f"{'signal':22s} {'n':>6s} {'P(up first)':>12s} {'edge vs base':>13s}")
    for name in sorted(hits, key=lambda k: rate(hits[k]), reverse=True):
        xs = hits[name]
        print(f"{name:22s} {len(xs):6d} {rate(xs):11.1f}% {rate(xs)-base_rate:+12.1f}%")
    print("\nedge > ~+3-5% AND large n = worth a closer look; else no edge. Multiple-testing: "
          "treat winners as hypotheses, confirm out-of-sample.")


if __name__ == "__main__":
    asyncio.run(main())

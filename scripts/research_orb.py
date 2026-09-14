"""Deep-dive on the one lead: Opening-Range Breakout (ORB).

Checks whether the +5.4% edge survives scrutiny:
  1) per-symbol — is it broad or driven by 1-2 names?
  2) train/test split (first 30d vs last 30d) — does it persist out-of-sample?
  3) barrier sensitivity (0.5 / 0.8 / 1.0%).

ORB long: on each day, OR = high of the first 30m (6 bars); signal = first 5m close above OR
high within the first ~2h. First-touch barrier ±B% within 60m. Offline (Yahoo 60d/5m), no cost.

Run: uv run python scripts/research_orb.py
"""

from __future__ import annotations

import asyncio

from apm.config import get_settings

DAYS = 60
K = 12          # 60m horizon
OR_LEN = 6      # 30m opening range
MAX_BAR = 24    # only signal within the first 2h of the session


def _fetch(symbol: str):
    import yfinance as yf

    df = yf.Ticker(symbol).history(period=f"{DAYS}d", interval="5m", auto_adjust=True)
    return (
        [float(x) for x in df["High"]],
        [float(x) for x in df["Low"]],
        [float(x) for x in df["Close"]],
        [ts.date() for ts in df.index],
    )


def _barrier_up(h, low, c, i, b) -> int | None:
    entry = c[i]
    up, dn = entry * (1 + b / 100), entry * (1 - b / 100)
    for j in range(i + 1, min(i + 1 + K, len(c))):
        if h[j] >= up and low[j] <= dn:
            return 1 if c[j] >= entry else 0
        if h[j] >= up:
            return 1
        if low[j] <= dn:
            return 0
    return None


def _orb_signals(h, low, c, days) -> list[int]:
    """Indices where an ORB long triggers."""
    idx = []
    n = len(c)
    i = 0
    while i < n:
        d = days[i]
        start = i
        while i < n and days[i] == d:
            i += 1
        end = i  # [start, end) is one day
        if end - start < OR_LEN + 2:
            continue
        or_high = max(h[start:start + OR_LEN])
        for k in range(start + OR_LEN, min(start + MAX_BAR, end)):
            if c[k] > or_high and c[k - 1] <= or_high:
                idx.append(k)
                break  # one ORB entry per day
    return idx


def _rate(xs):
    return (sum(xs) / len(xs) * 100) if xs else 0.0


async def main() -> None:
    symbols = get_settings().watchlist_symbols
    data = {}
    for sym in symbols:
        try:
            data[sym] = await asyncio.to_thread(_fetch, sym)
        except Exception as exc:  # noqa: BLE001
            print(f"{sym}: fetch failed {exc}")

    # Baseline P(up first) at 0.8 for reference (all bars).
    for b in (0.5, 0.8, 1.0):
        base = []
        for h, low, c, _days in data.values():
            for i in range(6, len(c) - 1):
                r = _barrier_up(h, low, c, i, b)
                if r is not None:
                    base.append(r)
        sig = []
        for h, low, c, days in data.values():
            for i in _orb_signals(h, low, c, days):
                r = _barrier_up(h, low, c, i, b)
                if r is not None:
                    sig.append(r)
        print(f"[barrier ±{b}%]  ORB n={len(sig):4d} P={_rate(sig):5.1f}%  "
              f"baseline={_rate(base):5.1f}%  edge={_rate(sig)-_rate(base):+.1f}%")

    print("\n--- per-symbol (barrier ±0.8%) ---")
    for sym, (h, low, c, days) in data.items():
        xs = [r for i in _orb_signals(h, low, c, days)
              if (r := _barrier_up(h, low, c, i, 0.8)) is not None]
        print(f"{sym:5s} n={len(xs):3d}  P(up first)={_rate(xs):5.1f}%")

    print("\n--- train/test split (first 30d vs last 30d, barrier ±0.8%) ---")
    for label, lo, hi in (("TRAIN(1st half)", 0.0, 0.5), ("TEST(2nd half)", 0.5, 1.0)):
        xs = []
        for h, low, c, days in data.values():
            uniq = sorted(set(days))
            keep = set(uniq[int(len(uniq) * lo):int(len(uniq) * hi)])
            for i in _orb_signals(h, low, c, days):
                if days[i] in keep and (r := _barrier_up(h, low, c, i, 0.8)) is not None:
                    xs.append(r)
        print(f"{label:16s} n={len(xs):4d}  P(up first)={_rate(xs):5.1f}%")


if __name__ == "__main__":
    asyncio.run(main())

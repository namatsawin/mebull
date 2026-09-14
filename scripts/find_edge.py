"""Find a statistical intraday edge for one or more stocks (no look-ahead, offline, no cost).

For each ticker: fetch 60d/5m from Yahoo, test a panel of candidate signals with an
OPTION-relevant first-touch barrier (does price hit +B% before -B% within K bars?), compare to
baseline, and split train/test (first vs last half) for signals that beat baseline.

Usage:  uv run python scripts/find_edge.py NVDA
        uv run python scripts/find_edge.py NVDA AAPL SPY
        uv run python scripts/find_edge.py NVDA --barrier 1.0 --horizon 12

A signal is a *hypothesis worth forward-testing* only if: edge vs baseline is clearly positive
(~+3-5%+), the sample is not tiny, AND it persists in the test half. Never "proven" — markets
are efficient and we test several signals (multiple-comparison risk).
"""

from __future__ import annotations

import statistics as st
import sys

DAYS = 60
VOL_WIN = 20
OR_LEN = 6      # 30m opening range


def _args(argv: list[str]) -> tuple[list[str], float, int]:
    syms, barrier, horizon = [], 0.8, 12
    it = iter(argv)
    for a in it:
        if a == "--barrier":
            barrier = float(next(it))
        elif a == "--horizon":
            horizon = int(next(it))
        elif not a.startswith("-"):
            syms.append(a.upper())
    return syms, barrier, horizon


def _fetch(symbol: str):
    import yfinance as yf

    df = yf.Ticker(symbol).history(period=f"{DAYS}d", interval="5m", auto_adjust=True)
    if df.empty:
        return None
    return (
        [float(x) for x in df["Open"]],
        [float(x) for x in df["High"]],
        [float(x) for x in df["Low"]],
        [float(x) for x in df["Close"]],
        [float(x) for x in df["Volume"]],
        [ts.date() for ts in df.index],
    )


def _rsi(c, i, n=14):
    if i < n:
        return None
    g = ll = 0.0
    for j in range(i - n + 1, i + 1):
        d = c[j] - c[j - 1]
        g += max(d, 0.0)
        ll += max(-d, 0.0)
    if ll == 0:
        return 100.0
    return 100 - 100 / (1 + (g / n) / (ll / n))


def _volz(v, i):
    if i < VOL_WIN:
        return None
    win = v[i - VOL_WIN:i]
    sd = st.pstdev(win)
    return (v[i] - sum(win) / len(win)) / sd if sd > 0 else None


def _barrier_up(h, low, c, i, b, k):
    entry = c[i]
    up, dn = entry * (1 + b / 100), entry * (1 - b / 100)
    for j in range(i + 1, min(i + 1 + k, len(c))):
        if h[j] >= up and low[j] <= dn:
            return 1 if c[j] >= entry else 0
        if h[j] >= up:
            return 1
        if low[j] <= dn:
            return 0
    return None


def _signals(o, h, low, c, v, days, i):
    out = []
    z = _volz(v, i)
    if z is not None and z >= 2 and c[i] > c[i - 3] and c[i] > c[i - 6]:
        out.append("momentum_volz")
    if i >= 20 and c[i] > max(h[i - 20:i]):
        out.append("donchian_breakout")
    r = _rsi(c, i)
    if r is not None and r < 25:
        out.append("rsi_oversold")
    day_start = next((k for k in range(i, -1, -1) if k == 0 or days[k - 1] != days[i]), i)
    if day_start + OR_LEN <= i < day_start + 24:
        orh = max(h[day_start:day_start + OR_LEN])
        if c[i] > orh and c[i - 1] <= orh:
            out.append("opening_range")
    if z is not None and z >= 1.5 and (c[i] - o[i]) / o[i] * 100 >= 0.3:
        out.append("big_green_vol")
    return out


def _rate(xs):
    return (sum(xs) / len(xs) * 100) if xs else 0.0


def analyze(sym: str, barrier: float, horizon: int) -> None:
    data = _fetch(sym)
    if data is None:
        print(f"\n{sym}: no data (bad ticker or Yahoo down)\n")
        return
    o, h, low, c, v, days = data
    if len(c) < VOL_WIN + horizon + 5:
        print(f"\n{sym}: not enough bars ({len(c)})\n")
        return
    base, sig = [], {}
    sig_by_half: dict[str, tuple[list, list]] = {}
    mid_day = sorted(set(days))[len(set(days)) // 2]
    for i in range(VOL_WIN, len(c) - 1):
        b = _barrier_up(h, low, c, i, barrier, horizon)
        if b is None:
            continue
        base.append(b)
        for name in _signals(o, h, low, c, v, days, i):
            sig.setdefault(name, []).append(b)
            tr, te = sig_by_half.setdefault(name, ([], []))
            (tr if days[i] < mid_day else te).append(b)
    br = _rate(base)
    print(f"\n=== {sym} — barrier ±{barrier}% within {horizon}x5m ({horizon*5}m), {DAYS}d ===")
    print(f"BASELINE  n={len(base):5d}  P(up first)={br:5.1f}%")
    print(f"{'signal':18s} {'n':>5s} {'P(up)':>7s} {'edge':>7s} {'train':>7s} {'test':>7s}")
    for name in sorted(sig, key=lambda k: _rate(sig[k]), reverse=True):
        xs = sig[name]
        tr, te = sig_by_half[name]
        verdict = "  <-- lead" if (_rate(xs) - br >= 3 and len(xs) >= 30) else ""
        print(f"{name:18s} {len(xs):5d} {_rate(xs):6.1f}% {_rate(xs)-br:+6.1f}% "
              f"{_rate(tr):6.1f}% {_rate(te):6.1f}%{verdict}")


def main() -> None:
    syms, barrier, horizon = _args(sys.argv[1:])
    if not syms:
        print("usage: uv run python scripts/find_edge.py TICKER [TICKER...] "
              "[--barrier 0.8] [--horizon 12]")
        return
    for s in syms:
        analyze(s, barrier, horizon)
    print("\nRead: 'edge' = signal P(up-first) minus baseline. A real lead needs edge clearly "
          "positive, decent n, AND train≈test (persists). Then FORWARD-test before real money.")


if __name__ == "__main__":
    main()

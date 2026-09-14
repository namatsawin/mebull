"""Find a statistical intraday edge for one or more stocks (no look-ahead, offline, no cost).

For each ticker: fetch 60d/5m from Yahoo, and for a panel of candidate signals SIMULATE the
trade — enter at the signal bar's close, exit at the first +B%/-B% barrier touch or at the K-bar
horizon — then report a consolidated summary table: strategy, stock, trades, win-rate, avg PnL,
total PnL, edge vs baseline, and an out-of-sample (2nd-half) check.

Usage:  uv run python scripts/find_edge.py NVDA
        uv run python scripts/find_edge.py SPY QQQ IWM --barrier 0.3
        uv run python scripts/find_edge.py NVDA AAPL --barrier 1.0 --horizon 12

PnL is gross % per trade (1 unit each), no fees/slippage. A row is a *lead* (hypothesis worth
forward-testing) only if vsBase is clearly positive, trades >= ~30, AND the 2nd half (OOS) also
beats baseline. Never "proven" — small sample + several signals = multiple-comparison risk.
"""

from __future__ import annotations

import statistics as st
import sys

DAYS = 60
VOL_WIN = 20
OR_LEN = 6      # 30m opening range
MIN_N_LEAD = 30


def _args(argv):
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


def _fetch(symbol):
    import yfinance as yf

    df = yf.Ticker(symbol).history(period=f"{DAYS}d", interval="5m", auto_adjust=True)
    if df.empty:
        return None
    return (
        [float(x) for x in df["Open"]], [float(x) for x in df["High"]],
        [float(x) for x in df["Low"]], [float(x) for x in df["Close"]],
        [float(x) for x in df["Volume"]], [ts.date() for ts in df.index],
    )


def _rsi(c, i, n=14):
    if i < n:
        return None
    g = ll = 0.0
    for j in range(i - n + 1, i + 1):
        d = c[j] - c[j - 1]
        g += max(d, 0.0)
        ll += max(-d, 0.0)
    return 100.0 if ll == 0 else 100 - 100 / (1 + (g / n) / (ll / n))


def _volz(v, i):
    if i < VOL_WIN:
        return None
    win = v[i - VOL_WIN:i]
    sd = st.pstdev(win)
    return (v[i] - sum(win) / len(win)) / sd if sd > 0 else None


def _sim_trade(o, h, low, c, i, b, k):
    """Return the trade's % result: exit at first +b/-b touch, else the K-bar horizon close.
    If both barriers hit in one bar, assume the stop hit first (conservative)."""
    entry = c[i]
    up, dn = entry * (1 + b / 100), entry * (1 - b / 100)
    for j in range(i + 1, min(i + 1 + k, len(c))):
        if low[j] <= dn:
            return -b
        if h[j] >= up:
            return +b
    end = min(i + k, len(c) - 1)
    return (c[end] / entry - 1) * 100


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


def _agg(rs):
    if not rs:
        return (0, 0.0, 0.0, 0.0)
    win = sum(1 for x in rs if x > 0) / len(rs) * 100
    avg = sum(rs) / len(rs)
    return (len(rs), round(win, 1), round(avg, 3), round(sum(rs), 1))


def analyze(sym, barrier, horizon):
    data = _fetch(sym)
    if data is None:
        return None, []
    o, h, low, c, v, days = data
    if len(c) < VOL_WIN + horizon + 5:
        return None, []
    mid = sorted(set(days))[len(set(days)) // 2]
    base, sig, sig_oos = [], {}, {}
    for i in range(VOL_WIN, len(c) - 1):
        base.append(_sim_trade(o, h, low, c, i, barrier, horizon))
        for name in _signals(o, h, low, c, v, days, i):
            ret = _sim_trade(o, h, low, c, i, barrier, horizon)
            sig.setdefault(name, []).append(ret)
            if days[i] >= mid:
                sig_oos.setdefault(name, []).append(ret)
    base_avg = sum(base) / len(base) if base else 0.0
    rows = []
    for name, rs in sig.items():
        n, win, avg, tot = _agg(rs)
        oos_avg = (sum(sig_oos.get(name, [])) / len(sig_oos[name])) if sig_oos.get(name) else 0.0
        vs = avg - base_avg
        lead = n >= MIN_N_LEAD and vs > 0 and oos_avg > base_avg
        rows.append({
            "stock": sym, "strategy": name, "n": n, "win": win, "avg": avg,
            "tot": tot, "vs": round(vs, 3), "oos": round(oos_avg, 3), "lead": lead,
        })
    return round(base_avg, 3), rows


def main():
    syms, barrier, horizon = _args(sys.argv[1:])
    if not syms:
        print("usage: uv run python scripts/find_edge.py TICKER [TICKER...] "
              "[--barrier 0.8] [--horizon 12]")
        return
    all_rows, base_by = [], {}
    for s in syms:
        base_avg, rows = analyze(s, barrier, horizon)
        if base_avg is None:
            print(f"{s}: no data (bad ticker or Yahoo down)")
            continue
        base_by[s] = base_avg
        all_rows += rows

    all_rows.sort(key=lambda r: r["vs"], reverse=True)
    print(f"\nEdge summary — barrier ±{barrier}% within {horizon}x5m ({horizon*5}m), {DAYS}d, "
          f"PnL = gross % per trade (no fees)\n")
    hdr = f"{'Stock':6s}{'Strategy':18s}{'Trades':>7s}{'Win%':>7s}{'AvgPnL%':>9s}" \
          f"{'TotPnL%':>9s}{'vsBase%':>9s}{'OOS%':>8s}  Lead"
    print(hdr)
    print("-" * len(hdr))
    for r in all_rows:
        flag = "🟢" if r["lead"] else ("🟡" if r["vs"] > 0 else "🔴")
        print(f"{r['stock']:6s}{r['strategy']:18s}{r['n']:7d}{r['win']:7.1f}"
              f"{r['avg']:9.3f}{r['tot']:9.1f}{r['vs']:+9.3f}{r['oos']:+8.3f}  {flag}")
    print("\nBaseline avg PnL%/trade (buy any bar):",
          "  ".join(f"{s}={a:+.3f}" for s, a in base_by.items()))
    print("🟢 lead: vsBase>0, trades>=30, OOS(2nd half)>baseline → forward-test candidate")
    print("🟡 positive but weak/small/failed-OOS   🔴 no edge")
    print("NOTE: gross underlying PnL, no spread/theta. Options need moves > ~5% spread+theta.")


if __name__ == "__main__":
    main()

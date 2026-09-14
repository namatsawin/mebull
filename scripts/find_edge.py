"""Find a statistical intraday edge for one or more stocks (no look-ahead, offline, no cost).

Tests a WIDE panel of candidate signals. For each: simulate the trade (enter at signal close,
exit at first +B%/-B% barrier or the K-bar horizon), then print two tables:
  1) per stock x strategy (Trades/Win%/AvgPnL%/TotPnL%/vsBase%/OOS%/Lead)
  2) per-strategy ROLLUP across all tickers (avg edge + on how many stocks it's a lead)

The rollup is the anti-overfitting view: a strategy that leads on MANY stocks is a real edge; a
lone winner among 20 signals is probably data-mining. Small sample (~60d 5m) + many signals =
high false-positive risk => a winner is a HYPOTHESIS to forward-test, never proof.

Usage:  uv run python scripts/find_edge.py NVDA
        uv run python scripts/find_edge.py SPY QQQ IWM --barrier 0.3
        uv run python scripts/find_edge.py NVDA AAPL --barrier 1.0 --horizon 12
"""

from __future__ import annotations

import statistics as st
import sys

DAYS = 60
WARMUP = 40
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


# --- indicator arrays (computed once per symbol) ----------------------------
def _ema(x, n):
    k = 2 / (n + 1)
    out = [x[0]]
    for val in x[1:]:
        out.append(out[-1] + k * (val - out[-1]))
    return out


def _rsi_arr(c, n=14):
    out = [None] * len(c)
    for i in range(n, len(c)):
        g = ll = 0.0
        for j in range(i - n + 1, i + 1):
            d = c[j] - c[j - 1]
            g += max(d, 0.0)
            ll += max(-d, 0.0)
        out[i] = 100.0 if ll == 0 else 100 - 100 / (1 + (g / n) / (ll / n))
    return out


def _vwap_arr(h, low, c, v, days):
    out, cpv, cv, cur = [], 0.0, 0.0, None
    for i in range(len(c)):
        if days[i] != cur:
            cur, cpv, cv = days[i], 0.0, 0.0
        tp = (h[i] + low[i] + c[i]) / 3
        cpv += tp * v[i]
        cv += v[i]
        out.append(cpv / cv if cv else c[i])
    return out


def _indicators(o, h, low, c, v, days):
    n = len(c)
    ema9, ema21 = _ema(c, 9), _ema(c, 21)
    ema12, ema26 = _ema(c, 12), _ema(c, 26)
    macd = [a - b for a, b in zip(ema12, ema26, strict=False)]
    macd_sig = _ema(macd, 9)
    rsi = _rsi_arr(c)
    vwap = _vwap_arr(h, low, c, v, days)
    sma20 = [None] * n
    std20 = [None] * n
    atr = [None] * n
    stochk = [None] * n
    rvol = [None] * n
    vz = [None] * n
    tr = [0.0] + [max(h[i] - low[i], abs(h[i] - c[i - 1]), abs(low[i] - c[i - 1]))
                  for i in range(1, n)]
    for i in range(n):
        if i >= 20:
            w = c[i - 20:i]
            sma20[i] = sum(w) / 20
            std20[i] = st.pstdev(w)
            vw = v[i - 20:i]
            m = sum(vw) / 20
            sd = st.pstdev(vw)
            rvol[i] = v[i] / m if m else None
            vz[i] = (v[i] - m) / sd if sd else None
        if i >= 14:
            atr[i] = sum(tr[i - 13:i + 1]) / 14
            hh, ll = max(h[i - 13:i + 1]), min(low[i - 13:i + 1])
            stochk[i] = (c[i] - ll) / (hh - ll) * 100 if hh > ll else 50.0
    # prior-day high/low mapped to each bar
    day_hi, day_lo, order = {}, {}, []
    for i in range(n):
        d = days[i]
        if d not in day_hi:
            day_hi[d], day_lo[d] = h[i], low[i]
            order.append(d)
        else:
            day_hi[d] = max(day_hi[d], h[i])
            day_lo[d] = min(day_lo[d], low[i])
    prev_of = {d: order[k - 1] for k, d in enumerate(order) if k > 0}
    pdh = [day_hi[prev_of[days[i]]] if days[i] in prev_of else None for i in range(n)]
    pdl = [day_lo[prev_of[days[i]]] if days[i] in prev_of else None for i in range(n)]
    # bars into day + day length
    bod, dstart = [0] * n, [0] * n
    cur, start = None, 0
    for i in range(n):
        if days[i] != cur:
            cur, start = days[i], i
        dstart[i] = start
        bod[i] = i - start
    day_len = {d: sum(1 for x in days if x == d) for d in order}
    dlen = [day_len[days[i]] for i in range(n)]
    return dict(ema9=ema9, ema21=ema21, macd=macd, macd_sig=macd_sig, rsi=rsi, vwap=vwap,
                sma20=sma20, std20=std20, atr=atr, stochk=stochk, rvol=rvol, vz=vz,
                pdh=pdh, pdl=pdl, bod=bod, dstart=dstart, dlen=dlen)


def _signals_at(ind, o, h, low, c, v, days, i):
    out = []
    g = ind
    up1 = c[i] > c[i - 1]
    # --- trend / momentum ---
    if g["vz"][i] and g["vz"][i] >= 2 and c[i] > c[i - 3] and c[i] > c[i - 6]:
        out.append("momentum_volz")
    if g["ema9"][i] > g["ema21"][i] and g["ema9"][i - 1] <= g["ema21"][i - 1]:
        out.append("ema9_21_cross")
    if g["macd"][i] > g["macd_sig"][i] and g["macd"][i - 1] <= g["macd_sig"][i - 1]:
        out.append("macd_cross")
    if c[i] > g["vwap"][i] and c[i - 1] <= g["vwap"][i - 1]:
        out.append("vwap_reclaim")
    if i >= 12 and c[i] > max(h[i - 6:i]) and low[i] > low[i - 6]:
        out.append("higher_high_low")
    # --- breakout ---
    if i >= 20 and c[i] > max(h[i - 20:i]):
        out.append("donchian20_break")
    ds = g["dstart"][i]
    if ds + 6 <= i < ds + 24:
        orh = max(h[ds:ds + 6])
        if c[i] > orh and c[i - 1] <= orh:
            out.append("opening_range")
    if g["pdh"][i] and c[i] > g["pdh"][i] and c[i - 1] <= g["pdh"][i]:
        out.append("prior_day_high_break")
    if g["sma20"][i] and g["std20"][i]:
        upper = g["sma20"][i] + 2 * g["std20"][i]
        lower = g["sma20"][i] - 2 * g["std20"][i]
        if c[i] > upper and c[i - 1] <= (g["sma20"][i - 1] + 2 * (g["std20"][i - 1] or 0)):
            out.append("bollinger_break_up")
        if c[i] < lower:
            out.append("bollinger_lower_revert")
    # --- mean reversion ---
    if g["rsi"][i] is not None and g["rsi"][i] < 25:
        out.append("rsi_oversold_25")
    if g["rsi"][i] is not None and g["rsi"][i] < 30:
        out.append("rsi_oversold_30")
    if g["stochk"][i] is not None and g["stochk"][i] < 20:
        out.append("stoch_oversold")
    if g["std20"][i] and g["std20"][i] > 0:
        z = (c[i] - g["vwap"][i]) / g["std20"][i]
        if z < -2:
            out.append("vwap_dev_low")
    # gap fade: day opened down >0.5% vs prior close, buy early
    if g["bod"][i] <= 3 and g["pdh"][i] and ds > 0:
        prev_close = c[ds - 1]
        day_open = o[ds]
        if prev_close and (day_open / prev_close - 1) * 100 <= -0.5 and up1:
            out.append("gap_down_fade")
    # --- volume / volatility ---
    if g["vz"][i] and g["vz"][i] >= 1.5 and (c[i] - o[i]) / o[i] * 100 >= 0.3:
        out.append("big_green_vol")
    if g["rvol"][i] and g["rvol"][i] >= 2 and up1:
        out.append("rvol_surge")
    if g["atr"][i] and i >= 20 and g["atr"][i - 6] and g["atr"][i] > g["atr"][i - 6] * 1.2 and up1:
        out.append("atr_expansion_up")
    # --- time of day ---
    if g["dlen"][i] and g["bod"][i] >= g["dlen"][i] - 12 and c[i] > c[i - 3]:
        out.append("power_hour_mom")
    if 6 <= g["bod"][i] <= 12 and c[i] > o[ds] * 1.003:
        out.append("opening_drive")
    return out


def _sim_trade(o, h, low, c, i, b, k):
    entry = c[i]
    up, dn = entry * (1 + b / 100), entry * (1 - b / 100)
    for j in range(i + 1, min(i + 1 + k, len(c))):
        if low[j] <= dn:
            return -b
        if h[j] >= up:
            return +b
    end = min(i + k, len(c) - 1)
    return (c[end] / entry - 1) * 100


def _agg(rs):
    win = sum(1 for x in rs if x > 0) / len(rs) * 100
    return len(rs), round(win, 1), round(sum(rs) / len(rs), 3), round(sum(rs), 1)


def analyze(sym, barrier, horizon):
    data = _fetch(sym)
    if data is None:
        return None, []
    o, h, low, c, v, days = data
    if len(c) < WARMUP + horizon + 5:
        return None, []
    ind = _indicators(o, h, low, c, v, days)
    mid = sorted(set(days))[len(set(days)) // 2]
    base, sig, oos = [], {}, {}
    for i in range(WARMUP, len(c) - 1):
        base.append(_sim_trade(o, h, low, c, i, barrier, horizon))
        for name in _signals_at(ind, o, h, low, c, v, days, i):
            ret = _sim_trade(o, h, low, c, i, barrier, horizon)
            sig.setdefault(name, []).append(ret)
            if days[i] >= mid:
                oos.setdefault(name, []).append(ret)
    base_avg = sum(base) / len(base) if base else 0.0
    rows = []
    for name, rs in sig.items():
        n, win, avg, tot = _agg(rs)
        oa = (sum(oos.get(name, [])) / len(oos[name])) if oos.get(name) else 0.0
        vs = round(avg - base_avg, 3)
        rows.append(dict(stock=sym, strategy=name, n=n, win=win, avg=avg, tot=tot,
                         vs=vs, oos=round(oa, 3),
                         lead=(n >= MIN_N_LEAD and vs > 0 and oa > base_avg)))
    return round(base_avg, 3), rows


def main():
    syms, barrier, horizon = _args(sys.argv[1:])
    if not syms:
        print("usage: uv run python scripts/find_edge.py TICKER [...] "
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

    if not all_rows:
        return
    hz = horizon * 5
    # --- ROLLUP: per strategy across all tickers (anti-overfit view) ---
    strat = {}
    for r in all_rows:
        s = strat.setdefault(r["strategy"], dict(n=0, wins=0.0, vs=[], leads=0, stocks=0))
        s["n"] += r["n"]
        s["vs"].append(r["vs"])
        s["leads"] += 1 if r["lead"] else 0
        s["stocks"] += 1
    print(f"\n=== STRATEGY ROLLUP (across {len(base_by)} tickers, ±{barrier}% / {hz}m) ===")
    print(f"{'Strategy':22s}{'Trades':>7s}{'AvgEdge%':>9s}{'Leads':>7s}  robustness")
    print("-" * 60)
    for name in sorted(strat, key=lambda k: sum(strat[k]["vs"]) / len(strat[k]["vs"]),
                       reverse=True):
        s = strat[name]
        avg_edge = sum(s["vs"]) / len(s["vs"])
        bar = "★" * s["leads"]
        print(f"{name:22s}{s['n']:7d}{avg_edge:+9.3f}{s['leads']:>4d}/{s['stocks']:<2d}  {bar}")

    print(f"\n=== PER STOCK x STRATEGY (leads + positives; ±{barrier}% / {hz}m) ===")
    hdr = f"{'Stock':6s}{'Strategy':22s}{'Trades':>7s}{'Win%':>7s}{'AvgPnL%':>9s}" \
          f"{'vsBase%':>9s}{'OOS%':>8s}  Lead"
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(all_rows, key=lambda r: r["vs"], reverse=True):
        if r["vs"] <= 0 and not r["lead"]:
            continue
        flag = "🟢" if r["lead"] else "🟡"
        print(f"{r['stock']:6s}{r['strategy']:22s}{r['n']:7d}{r['win']:7.1f}{r['avg']:9.3f}"
              f"{r['vs']:+9.3f}{r['oos']:+8.3f}  {flag}")
    print("\nBaseline PnL%/trade:", "  ".join(f"{s}={a:+.3f}" for s, a in base_by.items()))
    print("ROLLUP 'Leads x/y' = on how many tickers it's a 🟢 lead → the anti-overfit signal. "
          "Many-stock leads = real; lone leads among 20 signals = likely noise. Forward-test "
          "before real money. Gross underlying PnL (no spread/theta).")


if __name__ == "__main__":
    main()

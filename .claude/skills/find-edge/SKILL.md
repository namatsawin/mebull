---
name: find-edge
description: Research whether a specific stock has a statistical intraday trading edge before trading it. Use when the user wants to find/validate an edge for a ticker (e.g. "find edge for NVDA", "does AAPL have an edge", "check TSLA before I trade"). Asks for the ticker if not given, runs a no-look-ahead signal panel with an option-relevant barrier metric + train/test split over Yahoo data, and reports an honest verdict (edge vs no-edge, with caveats). Never enables real trading.
---

# Find Edge for a Stock

Purpose: given a ticker, determine whether any simple intraday signal has a *statistical edge*
on that stock — BEFORE risking money. This encodes the honest research workflow: markets are
mostly efficient, so the default expectation is "no edge," and any winner is a hypothesis to
forward-test, not proof.

## Steps

1. **Get the ticker(s).** If the user didn't name a stock, ask: "Which stock(s)? (e.g. NVDA)".
   Accept one or many. (ETFs work too, but note ORB rarely triggers on ETFs.)

2. **Run the edge finder** (offline, free Yahoo data, no orders):
   ```bash
   uv run python scripts/find_edge.py <TICKER> [<TICKER> ...]
   ```
   Optional knobs: `--barrier 1.0` (the ±% first-touch move; default 0.8) and `--horizon 12`
   (5m bars ahead; default 12 = 60 min). Larger barrier = a bigger move must happen (harder,
   but more meaningful for options which pay spread+theta).

3. **Interpret the summary table honestly** (this is the important part — do NOT hype).
   The script prints ONE consolidated table (each row = stock × strategy), sorted best-first:

   | Column | Meaning |
   |---|---|
   | Stock / Strategy | the ticker and the signal |
   | Trades | number of simulated trades (signal fires → enter → exit at barrier/horizon) |
   | Win% | % of those trades that closed positive |
   | AvgPnL% | average gross % per trade (no fees) |
   | TotPnL% | sum of per-trade % (rough cumulative, 1 unit each) |
   | vsBase% | AvgPnL minus the "buy any bar" baseline — **the actual edge** |
   | OOS% | avg PnL in the 2nd half of the window (out-of-sample check) |
   | Lead | 🟢 real lead / 🟡 weak / 🔴 no edge |

   - **🟢 lead** = `vsBase>0` AND `Trades≥30` AND `OOS>baseline` (persists out-of-sample) →
     the only rows worth a forward test.
   - **🔴/🟡** = edge ≤ 0, or too few trades, or fails out-of-sample → treat as no edge.
   - Report the winners as a short bullet list (stock + strategy + win% + edge), then the caveats.
   - Caveats to always state: small sample (~60 trading days of 5m), several signals tested
     (multiple-comparison risk — a lone 🟢 could be luck; multiple stocks sharing the same
     winning signal is stronger), and this is the *underlying* gross PnL — a long option must
     beat spread (~5%) + theta, so a small underlying edge ≈ negative after option costs (better
     expressed in shares).

4. **Recommend next step:**
   - If a lead survives: propose a **forward test** (paper, out-of-sample by time) before any
     real money — re-slicing the same 60 days is not confirmation.
   - If nothing survives: say so plainly ("no edge found — don't trade it"). That's a valid,
     valuable result.

## Hard rules
- This skill NEVER enables real trading, changes `APM_TRADING_ENABLED`, or places orders. It is
  research only.
- Never claim a "proven" or "guaranteed" edge. Frame everything as evidence + hypothesis.
- If Yahoo fails / bad ticker, report it plainly and stop (don't fabricate numbers).

## Output has TWO tables
1. **STRATEGY ROLLUP** (read this first) — per-signal aggregate across ALL tickers: total
   trades, avg edge, and **Leads x/y** = on how many tickers it's a 🟢 lead. This is the
   anti-overfitting view: a signal that leads on *many* tickers is a real edge; a lone winner
   among ~20 signals is likely data-mining. Prefer high `Leads x/y`, not the single best row.
2. **PER STOCK × STRATEGY** — the detail rows (positives/leads), sorted by edge.

## Signals tested (~20, all bullish long, no-look-ahead)
Trend/momentum: `momentum_volz`, `ema9_21_cross`, `macd_cross`, `vwap_reclaim`,
`higher_high_low`. Breakout: `donchian20_break`, `opening_range`, `prior_day_high_break`,
`bollinger_break_up`. Mean-reversion: `rsi_oversold_25/30`, `stoch_oversold`, `vwap_dev_low`,
`bollinger_lower_revert`, `gap_down_fade`. Volume/vol: `big_green_vol`, `rvol_surge`,
`atr_expansion_up`. Time-of-day: `power_hour_mom`, `opening_drive`.

Empirically (this universe, 60d): the **mean-reversion family leads across most tickers**;
trend/breakout/momentum consistently show no edge. To add/adjust, edit
`scripts/find_edge.py` (`_signals_at`).

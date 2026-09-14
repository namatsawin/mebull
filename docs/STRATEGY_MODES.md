# Strategy modes — `ai` vs `quant` (the "B" architecture)

The same infrastructure (Safety Guard, journal, reconcile, execution, learning) runs under
both modes. Only **who makes the per-cycle decision** changes. Set with `APM_STRATEGY_MODE`.

```
                 every 5-min cycle                       a few times/day
   ai    │  ContextBuilder → Claude.analyze() → Decision │  (n/a)
   quant │  ContextBuilder → QuantRuleProvider → Decision │  AI supervisor → SupervisorPolicy
         │        (deterministic, NO AI)                  │        (Claude, 1 call)
```

## `ai` (default)
Claude decides every cycle from the full context. Most flexible; ~1 API call per cycle.
Good when the rules aren't fully specified. This is `src/apm/decision/provider.py`
(`AnthropicProvider`).

## `quant` — deterministic rules + AI supervisor
The point: **use AI sparingly, not every cycle.** A fully-specified strategy is encoded as
deterministic rules (reproducible, backtestable, cheap); the AI is reserved for high-level
judgment it actually adds edge to.

- **`QuantRuleProvider`** (`src/apm/strategy/rules.py`) — implements the same
  `analyze(context) → Decision` interface but with NO AI. One decision per cycle, prioritized:
  1. **EXIT** — options by premium P&L (+TP / −SL); equities by intraday level/trend.
  2. **ENTER** — the strongest momentum name that the breadth/regime corroborate, with
     acceptable reward:risk, sized to per-trade risk and affordable buying power. With
     `APM_OPTIONS_ENABLED` it expresses a bullish setup as a **long CALL** (Model B).
  3. **WAIT** — otherwise (first-class; never forces a trade).
- **`SupervisorPolicy`** (`src/apm/strategy/policy.py`, table `supervisor_policy`) — the knobs
  the rules obey: `regime`, `trade_today`, `allow_longs/shorts`, `risk_multiplier`,
  `max_positions`, entry filters, `veto_symbols`. The rules load the latest active row each
  cycle (deterministic; a safe default is used until the supervisor first runs).
- **`SupervisorService`** (`src/apm/supervisor/service.py`) — runs every
  `APM_SUPERVISOR_INTERVAL_MINUTES` (default 120). Backends: `anthropic` (one Claude call
  producing a validated policy) or a deterministic heuristic fallback. **Never places orders.**

Token cost drops from ~1 call/cycle (`ai`) to a few calls/day (`quant`).

## Model B options (single-leg long)
When `APM_OPTIONS_ENABLED=true` and quant mode:
- **Entry**: pick an ATM-ish CALL (|delta| in `[APM_OPTION_DELTA_MIN, APM_OPTION_DELTA_MAX]`),
  affordable (`cost ≤ APM_OPTION_MAX_PREMIUM_PCT_NAV%` of NAV and ≤ buying power), liquid
  (spread ≤ `APM_OPTION_MAX_SPREAD_PCT%`), `APM_OPTION_MAX_CONTRACTS` (default 1). BUY_TO_OPEN.
- **Exit**: sell-to-close on premium `+APM_OPTION_TAKE_PROFIT_PCT%` / `−APM_OPTION_STOP_LOSS_PCT%`.
- **Intraday-flat**: the EOD flatten closes option positions too (sell-to-close at bid) — a
  held 0DTE must never reach expiry. This is the hard backstop regardless of the model's exits.

> ⚠️ Small-account reality: on a tiny NAV, one option contract can be a large % of the account
> (premium = max loss). `APM_OPTION_MAX_PREMIUM_PCT_NAV` bounds the concentration you accept.

## Data layers
- **Real-time (Webull)**: Nasdaq Basic quotes/snapshots ✅ + OPRA options (chain STANDARD-only,
  greeks/IV/OI via `get_option_snapshot`). Used for live price/spread/greeks + execution.
- **Historical (free, Yahoo)**: Webull's bars endpoint 404s for this entitlement, so daily+5m
  bars and `^VIX` are seeded into `price_bar` at startup (`marketdata/history.py`, idempotent,
  graceful). Powers **HV20**, **5m volume_z** (Model B order-flow trigger), and **VIX** (supervisor
  risk sizing). Data-only; never execution. yfinance is unofficial + ~15m delayed — fine for the
  historical/context layer; the live trigger still uses Webull real-time.
- Intraday snapshot (Webull) still drives support/resistance/ATR/trend (day range); Yahoo adds
  the longer-horizon stats the snapshot can't (HV, volume baseline, VIX).

## Scope: Model B only (Model A is PARKED)
The build focuses on **Model B (intraday 0DTE long calls)** — the only model that fits a small
cash account. **Model A (short credit spreads) is intentionally NOT built / parked**: it needs
multi-leg orders + margin + far more capital (a $5 spread's max loss ≫ the 2-3% NAV sizing on a
~$700 account). The rules engine only ever buys single-leg long options; there is no code path
that opens a spread. Revisit Model A when capital + multi-leg support exist.

## Still missing / next (Model B)
- **Backtest** — the rules are NOT yet validated to have edge. Highest-priority next step.
- **VRP context (IV − HV20)** — computable now (OPRA IV + Yahoo HV); a better "is premium rich?"
  read than iv_rank for the small account.
- **iv_rank_30d** — needs accumulated IV history (self-collect ~30d) or a vendor.
- **Trailing stop + "flat N candles → exit"** — Model B exit refinements (currently fixed TP/SL).
- **Settled-cash / GFV** tracking (cash account, T+1) — not modeled yet.

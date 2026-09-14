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

## Data notes (this account's entitlement)
- Quotes/snapshots: Nasdaq Basic ✅. **Historical bars endpoint 404s** → technicals + breadth
  are derived from the intraday **snapshot** (day open/high/low/prev-close), not bars.
- Options: need **OPRA**. Chain via `get_option_contracts` (STANDARD contracts only — adjusted
  `2GOOG`/FLEX are filtered out), greeks/IV/OI via `get_option_snapshot`.
- No index category → **no VIX** via the API (proxy with an ETF or realized vol if needed).

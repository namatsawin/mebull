# Project Status & Next Steps (handoff)

For the next person (or AI) picking this up. Read `CLAUDE.md` first (rules + layout), then
`docs/HOW_IT_WORKS.md`. This file = what's done, what's stubbed, and what to do next.

_Last updated: 2026-09-15._

## TL;DR
The full system (M0–M11) is **built, tested (91 tests), and running LIVE in Docker on the real
Webull account** (`APM_EXECUTION_MODE=REAL`, `APM_TRADING_ENABLED=true`, market-hours-only).
It runs as an **intraday day-trader** in **quant mode** (deterministic rules + AI supervisor)
with **options (Model B) enabled**. Decisions fire on a wall-clock-aligned 5-min timer; every
cycle's full reasoning is persisted to `audit_log`. Kill switch: `APM_TRADING_ENABLED=false`
(+ recreate app) or the DB-backed half.

## What works today (LIVE-verified against the real account)
- `docker compose up -d` → Postgres + always-on app; migrations auto-run; health at :8080.
- Real auth (Thai account, cached token), balance (multi-currency USD sub-account), real
  quotes + **snapshot-derived technicals & breadth** (the bars endpoint 404s for this
  entitlement, so day open/high/low/prev-close drive support/resistance/ATR/trend/regime).
- **Equity order path** verified: `EQUITY` instrument_type + `support_trading_session` +
  `entrust_type` (both LIMIT and MARKET previews accepted).
- **Options (OPRA)**: chain (STANDARD contracts only) + greeks/IV/OI via option snapshot; **v3
  option order path** verified (BUY_TO_OPEN / SELL_TO_CLOSE previews OK).
- **Quant mode (B)**: deterministic `QuantRuleProvider` decides each cycle; AI `SupervisorService`
  sets `SupervisorPolicy` a few times/day (verified: it stood the book down in a risk-off tape).
- **Intraday-flat**: EOD flatten closes equities (MARKET) and options (sell-to-close at bid).
- Safety Guard (un-bypassable, fail-closed) + kill switch; guarded execution lifecycle;
  learning/backtest/strategy/experiments/reviews; full `audit_log` per cycle.
- Verify anytime (read-only previews): `uv run python scripts/verify_real_orderpath.py`.

## Known caveats / not verified live
1. **Real fills** — previews are verified; actual fill + real option-position **repricing** for
   premium TP/SL exits isn't fully proven (the EOD flatten is the hard backstop). Watch the
   first live fills.
2. **Real-time order events (MQTT)** — not wired; fills are polled (`get_order`).
3. **Partial fills** — PARTIAL_FILLED path unexercised (mock fills all-or-nothing).
4. **No VIX / historical bars** for this entitlement (see docs/STRATEGY_MODES.md).
5. **Small account**: one option contract can be a large % of NAV — bounded by
   `APM_OPTION_MAX_PREMIUM_PCT_NAV`.

## Not built yet — Tier 2 (makes it truly autonomous; no creds needed)
6. **Market discovery** (`src/apm/discovery/`) — stub. The hook exists
   (`ContextBuilder(discovery=...)`) but nothing scans gainers/volume/volatility, so the AI
   only "sees" the watchlist + current holdings. This is the biggest capability gap.
7. **News / earnings / catalysts** — not wired.
8. **Real observability counters** — `/metrics` is up but no counters are registered
   (Claude latency, safety blocks, fill latency, tokens).
9. **Claude cost/usage monitoring** (spec §60).

## Nice-to-have — Tier 3
10. ~~Market-hours guard~~ ✅ **DONE** — `APM_MARKET_HOURS_ONLY=true` (default) skips cycles
    when the US market is closed (weekends/holidays/after-hours, DST-aware);
    `src/apm/marketdata/hours.py`. Update the holiday list annually.
11. ~~Options (single-leg)~~ ✅ **DONE (M10) + LIVE (OPRA)** — CALL/PUT through the full
    pipeline (chain+greeks → priced LIMIT → guard → journal), v3 order path live-verified.
    Model B rules (long calls) in quant mode. Caveats: single-leg only (no spreads);
    trade-book P&L per-contract (not ×100 yet).
11b. ~~Quant mode (B)~~ ✅ **DONE (M11)** — deterministic rules + AI supervisor
    (`APM_STRATEGY_MODE=quant`). See docs/STRATEGY_MODES.md.
12. Backtest realism (point-in-time data feed, corporate actions, survivorship).
12. Retry/backoff on Webull + Anthropic calls; options order legs; CI (GitHub Actions);
    fill in `tests/webull_sandbox/` against the live sandbox.

## How to continue (quick start for the next dev)
```bash
uv sync --extra dev
docker compose up -d db
export APM_DATABASE_URL=postgresql+asyncpg://apm:apm@localhost:5432/apm
uv run pytest -q            # 71 tests; integration auto-skips if DB is down
uv run ruff check .
uv run apm                  # run the orchestrator locally (MOCK mode)
```
- Add a table? Create the model in `src/apm/db/models.py`, then
  `uv run alembic revision --autogenerate -m "..."` and `alembic upgrade head`.
- Switch to the real Claude: set `APM_CLAUDE_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`.
- Recommended model: `claude-opus-4-8` (see cost notes in chat history / `docs/`).

## Conventions & invariants (do not break)
See `CLAUDE.md` → "Non-negotiable invariants". In short: Safety Guard is un-bypassable;
kill switch halts real orders; reconcile before real orders; idempotent orders; secrets in
env only; Decision≠Order≠Execution≠Trade; WAIT is first-class; research never touches the
account; fail closed when unsure.

## Suggested next move
If you have creds → **Tier 1** (SANDBOX-verify the adapter + provider).
If not → **Tier 2 market discovery** (highest-value, no creds). The market-hours guard that
cuts ~65% of cost is already in place (`APM_MARKET_HOURS_ONLY`).

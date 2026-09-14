# Project Status & Next Steps (handoff)

For the next person (or AI) picking this up. Read `CLAUDE.md` first (rules + layout), then
`docs/HOW_IT_WORKS.md`. This file = what's done, what's stubbed, and what to do next.

_Last updated: 2026-09-14._

## TL;DR
The full system (M0–M9) is **built, tested (71 tests), and runs in Docker** in `MOCK` mode
with zero external credentials. It is **not yet safe for real money** — that's gated on live
verification that needs your Webull/Anthropic credentials. The AI decides freely on a
wall-clock-aligned timer (default every 5 min); there is no meaningful-event gate.

## What works today (verified)
- `docker compose up -d` → Postgres + always-on app; migrations auto-run; health endpoints.
- Interval decision loop, clock-aligned (:00/:05/:10…). `APM_DECISION_INTERVAL_SECONDS`.
- Full decision cycle: build state → build context → Claude (mock provider) → validate →
  journal (incl. WAIT + counterfactual seeds) → optional guarded execution → reconcile.
- Safety Guard (un-bypassable, fail-closed) + kill switch (`apm-killswitch`).
- Execution lifecycle end-to-end **against the mock broker** (buy/close/PnL, idempotency).
- Learning (counterfactual eval + metrics), backtest (no look-ahead), strategy versioning,
  experiments, reviews.

## What is written but NOT verified live (needs your creds) — Tier 1 blockers
These are the gate to real money. See `docs/PHASE0_WEBULL_CHECKLIST.md`.
1. **`RealWebullAdapter`** (`src/apm/webull/real.py`) — SDK calls are correct, but every
   response/order JSON field name is a best-guess with a `_first(...)` fallback. **Run against
   Webull SANDBOX and fix the field mappings** before trusting REAL orders.
2. **`AnthropicProvider`** (`src/apm/decision/provider.py`) — only tested with a mocked
   client (to avoid spend). Verify one real API call end-to-end.
3. **Real-time order events (MQTT)** — not wired. Execution monitors fills by **polling**
   (`get_order`). For REAL, consider the SDK's `trade_events_client` for push fills.
4. **Partial fills** — the mock fills all-or-nothing; the PARTIAL_FILLED path is unexercised.
5. **Webull ToS** — confirm automated trading is permitted + rate limits.

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
11. Backtest realism (point-in-time data feed, corporate actions, survivorship).
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

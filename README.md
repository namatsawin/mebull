# Autonomous AI Portfolio Manager

One persistent Claude identity that autonomously manages **one real Webull portfolio** —
discovering opportunities, deciding `BUY / SELL / CLOSE / HOLD / WAIT / REBALANCE /
RESEARCH / EXPERIMENT`, executing through an infrastructure-level **Safety Guard**,
journaling every decision, and learning from outcomes and counterfactuals.

The owner provides the portfolio (and optionally a watchlist). The owner does **not**
define the strategy, position size, entry/exit, or timing — Claude decides, or **WAITs**.

> ⚠️ Real money. Start with `APM_TRADING_ENABLED=false` and `APM_EXECUTION_MODE=MOCK`.
> "Buildable" is not "profitable" — there is no guaranteed trading edge. See `docs/`.

## Quick start (background via Docker)

```bash
cp .env.example .env          # fill secrets; leave trading disabled to start
docker compose up -d --build  # postgres + always-on app
curl localhost:8080/status    # non-secret operational snapshot
docker compose logs -f app
```

## Stack
Python 3.12 · asyncio · Postgres + SQLAlchemy(async)/Alembic · interval decision loop ·
Anthropic API (pay-per-token) · Webull official Python SDK · structlog · FastAPI health.

## Where things are
- `CLAUDE.md` — context for AI coding sessions (read this first).
- `docs/ARCHITECTURE.md` — component map, data flow, invariants.
- `docs/RUNBOOK.md` — run, kill switch, migrations, rollout.
- `docs/PHASE0_WEBULL_CHECKLIST.md` — verify before enabling REAL.
- `claude/system-prompt.md` — the portfolio-manager identity/prompt.
- `src/apm/` — the system, one module per responsibility.

## Milestones — all implemented ✅
M0 scaffold → M1 Webull adapter → M2 portfolio+reconcile → M3 journal+memory →
M4 Claude engine → M5 interval decision loop → M6 Safety Guard+kill switch → M7 guarded execution
→ M8 learning+backtest+strategy/experiment/reviews → M9 REAL (flag-gated).

**Before enabling REAL:** complete `docs/PHASE0_WEBULL_CHECKLIST.md` and run against Webull
SANDBOX first to verify the SDK response/order field mappings flagged in
`src/apm/webull/real.py`. Runs today end-to-end in `MOCK` mode with zero external creds.

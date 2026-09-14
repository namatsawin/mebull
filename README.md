# Autonomous AI Trader

An autonomous trader for **one real Webull portfolio** — deciding
`BUY / SELL / CLOSE / HOLD / WAIT / REBALANCE / RESEARCH / EXPERIMENT`, executing through an
infrastructure-level **Safety Guard**, journaling every decision, and learning from outcomes.

Two **strategy modes** (`APM_STRATEGY_MODE`):
- **`ai`** — Claude decides every cycle.
- **`quant`** — a deterministic rules engine decides every cycle (no AI in the hot path) + an
  AI **supervisor** sets policy a few times/day. Cheap + reproducible ("B"). See
  `docs/STRATEGY_MODES.md`.

Runs as an **intraday day-trader** (closes same day; force-flattens near the close) and can
trade **stocks/ETFs and single-leg options** (Model B long calls/puts; needs OPRA).

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
→ M8 learning+backtest+strategy/experiment/reviews → M9 REAL (flag-gated) →
M10 options (Model B) → M11 quant mode (deterministic rules + AI supervisor).

**Status:** running LIVE on the real account (REAL + quant + options). The equity and v3 option
order paths are live-verified; verify anytime with `uv run python scripts/verify_real_orderpath.py`
(read-only previews). See `docs/STATUS.md` for the current state and caveats.

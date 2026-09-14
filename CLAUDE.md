# CLAUDE.md — context for AI coding sessions

Read this first. It orients you fast and encodes the non-negotiable rules. Picking the
project up? Also read `docs/STATUS.md` (what's done vs stubbed + next steps).

## What this is
An **Autonomous AI trader** managing one **real** Webull portfolio, behind an
infrastructure-level Safety Guard. Built from the user's spec v2.0 (referenced as §N
throughout the code). See `docs/ARCHITECTURE.md`.

Two **strategy modes** (`APM_STRATEGY_MODE`), same infra/guard/journal underneath:
- **`ai`** — Claude decides every cycle (flexible; ~1 API call/cycle).
- **`quant`** (the "B" architecture) — a **deterministic rules engine** decides every cycle
  with NO AI in the hot path; a lightweight **AI supervisor** runs a few times/day
  (`APM_SUPERVISOR_INTERVAL_MINUTES`) to set the `SupervisorPolicy` (regime, risk multiplier,
  vetoes) the rules read. Cheap + reproducible. See `docs/STRATEGY_MODES.md`.

Current live config is an **intraday day-trader** (`APM_TRADING_STYLE=daytrade`,
`APM_INTRADAY_ONLY=true`): positions are closed the same day; the loop force-flattens all
positions (equities AND options) within `APM_FLATTEN_BEFORE_CLOSE_MINUTES` of the close.
**Options** (Model B: single-leg long calls/puts) are supported when `APM_OPTIONS_ENABLED=true`
(needs the Webull OPRA subscription). Every cycle's full reasoning is persisted to `audit_log`.

## Locked decisions (do not relitigate without the user)
- **Language: Python 3.12**, `asyncio`. Chosen because Webull's official SDK is Python/Java
  only and real-execution safety favors the official SDK.
- **AI provider: Anthropic API key (pay-per-token, Commercial Terms)**, behind a
  `ClaudeProvider` interface. Do NOT wire a Claude.ai subscription/OAuth (Pro/Max/Claude
  Code login) into this always-on service — it risks account bans for unattended automation.
- **Persistence: Postgres** via SQLAlchemy 2.0 async + Alembic. DB is a *representation*;
  **Webull is the source of truth** for the account.
- **Runs unattended in Docker** (`docker compose up`); the app is a long-lived asyncio process.
- **REAL trading only** — no paper environment, no `execution_type`/`PAPER` field (spec §6, §19).

## Non-negotiable invariants (never violate)
1. **Safety Guard is un-bypassable** (`src/apm/safety`). Every order routes through
   `SafetyGuard.authorize(...)`. Claude/decision code has no path that skips it (spec §34, §38).
2. **Kill switch** (spec §36): `APM_TRADING_ENABLED=false` OR non-REAL mode ⇒ zero real
   orders. Analysis/research/journaling still run. There is also a DB-backed half (M6).
3. **Reconcile before important real orders** (spec §31-32); block new discretionary trades
   on any local↔Webull mismatch, stale state, or DB/data/API outage (spec §37).
4. **Idempotency** (spec §33): `client_order_id = fn(decision_id, leg)`; on submit timeout,
   reconcile before any retry — never blind-resubmit.
5. **Secrets** (spec §62-63): env only. Never in prompts, DB, journal, or logs. Use
   `Settings.redacted()` and the logging redaction processor. Add new secret keys to
   `_SENSITIVE_KEYS` in `src/apm/observability/logging.py`.
6. **Decision ≠ Order ≠ Execution ≠ Trade** — keep them as separate records (spec §18).
7. **WAIT is a first-class decision** and must be journaled with considered/rejected
   opportunities (spec §15-17). The system must never force a trade.
8. **Research never touches the real account** (spec §7, §29).

## Layout
```
src/apm/
  config.py           env-only settings, kill switch, execution mode
  observability/      structlog + secret redaction, metrics
  db/                 async engine/session, ORM base, (repositories M2+)
  orchestrator/       main.py (entrypoint), health.py, migrate.py
  webull/ marketdata/ portfolio/ reconcile/ journal/ memory/ decision/
  discovery/ safety/ execution/ learning/ research/
  strategy/           quant "B": rules.py (deterministic decider), policy.py, options.py
  supervisor/         AI supervisor (sets SupervisorPolicy a few times/day, quant mode)
migrations/           Alembic (async env.py); models register on Base.metadata
claude/               system-prompt.md, decision-schema.json, prompts/
docs/                 ARCHITECTURE / RUNBOOK / PHASE0_WEBULL_CHECKLIST
tests/                unit / integration / webull_sandbox / failure / e2e
```

## How to run / verify
```bash
uv sync --extra dev
docker compose up -d --build      # background app + postgres
uv run pytest                     # unit tests
uv run ruff check . && uv run mypy
curl localhost:8080/status        # non-secret snapshot
```
Migrations run in-process at startup (`orchestrator/migrate.py`). To add tables: create
models under `apm/db/models` (import them so they register on `Base.metadata`), then
`uv run alembic revision --autogenerate -m "..."`.

## Milestones (all implemented)
M0 scaffold ✅ · M1 Webull adapter (Protocol+Mock+Real) ✅ · M2 portfolio+reconcile ✅ ·
M3 journal+memory ✅ · M4 Claude engine ✅ · M5 interval decision loop ✅ · M6 Safety Guard+kill
switch ✅ · M7 execution (guarded lifecycle) ✅ · M8 learning+backtest+strategy/experiment/
reviews ✅ · M9 REAL flag-gated ✅ · M10 options (Model B) ✅ · M11 quant mode (deterministic
rules + AI supervisor, "B") ✅.

**REAL is LIVE and verified** against the account: equity order path (EQUITY +
`support_trading_session`), snapshot-derived technicals + breadth (the bars endpoint 404s for
this entitlement), and the v3 **option order path** (top strategy fields + a leg keyed by
underlying + option_type/strike/expiry; `position_intent` BUY_TO_OPEN/SELL_TO_CLOSE). Options
need the **OPRA** subscription (chain/greeks/IV via `get_option_snapshot`). Verify live with
`uv run python scripts/verify_real_orderpath.py` (read-only previews).

## Enabling REAL (do not skip)
1. Complete docs/PHASE0_WEBULL_CHECKLIST against the live account.
2. Run against SANDBOX first (`APM_EXECUTION_MODE=SANDBOX`, real WEBULL_* creds) and verify
   the order lifecycle + reconciliation; fix any `_first(...)` field mappings in real.py.
3. Only then set `APM_EXECUTION_MODE=REAL` + `APM_TRADING_ENABLED=true`. `apm-killswitch on`
   halts all new orders instantly.

## Conventions
- Reference the spec section (`spec §N`) in docstrings when implementing a requirement.
- New env vars: add to `Settings`, `.env.example`, and (if secret) redaction + `redacted()`.
- Prefer failing closed: when unsure whether it's safe to trade, don't.

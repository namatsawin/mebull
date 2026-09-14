# Runbook

## Run in background (Docker)

```bash
cp .env.example .env        # fill in secrets; keep APM_TRADING_ENABLED=false initially
docker compose up -d --build        # starts postgres + app (restart: unless-stopped)
docker compose logs -f app          # structured JSON logs (no secrets)
docker compose --profile tools up -d adminer   # optional DB browser at :8081
```

Health/status:
```bash
curl localhost:8080/health     # liveness
curl localhost:8080/ready      # readiness (DB reachable)
curl localhost:8080/status     # non-secret operational snapshot
curl localhost:8080/metrics    # prometheus metrics
```

Stop / restart:
```bash
docker compose down            # keeps pgdata volume
docker compose restart app
```

## Local dev (no Docker for the app)

```bash
uv sync --extra dev
docker compose up -d db                     # just Postgres
export APM_DATABASE_URL=postgresql+asyncpg://apm:apm@localhost:5432/apm
uv run apm                                  # run the orchestrator
uv run pytest                               # tests
uv run ruff check . && uv run mypy          # lint + types
```

## Kill switch / emergency stop (spec §36)
Two independent halves; either one off ⇒ NO real orders (analysis/research continue):
1. **Env:** `APM_TRADING_ENABLED=false` (restart app to apply).
2. **DB-backed flag:** enforced at authorize-time by the Safety Guard (M6).

To fully halt real trading right now: set `APM_TRADING_ENABLED=false` and
`APM_EXECUTION_MODE=MOCK`, then `docker compose up -d app`.

## Execution modes (spec §67)
`APM_EXECUTION_MODE`: `MOCK` (no broker) → `SANDBOX` (Webull sandbox, no capital) →
`REAL` (live). REAL also requires `APM_TRADING_ENABLED=true` and a passing Phase 0
checklist (see PHASE0_WEBULL_CHECKLIST.md).

## Migrations
Run automatically at startup (in-process). Manual:
```bash
uv run alembic revision --autogenerate -m "add X"
uv run alembic upgrade head
```

## Reconciliation (spec §32) — M2+
On startup / interval / after order events / before important real orders. On any
local↔Webull mismatch: new discretionary trades are blocked until reconciled.

## Rollout phases (spec §67)
read-only → research/backtest → autonomous analysis → sandbox → REAL monitoring only →
REAL execution behind Safety Guard → expanded autonomy after stability. There is NO paper phase.

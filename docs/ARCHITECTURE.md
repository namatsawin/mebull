# Architecture

Source spec: the user's *Autonomous AI Portfolio Manager v2.0* (section refs like §30 below).

## The one rule (spec §79)

```
Claude decides WHAT should happen.
Infrastructure decides WHETHER it is SAFE to execute.
Webull decides WHAT ACTUALLY happened.
Database remembers WHAT happened.
Learning Engine determines WHAT WAS LEARNED.
Claude uses that knowledge for the NEXT decision.
```

## Flow (spec §80-81)

```
Owner ──(optional watchlist)──▶ Portfolio Identity
        │
        ▼
Always-On Infrastructure  (webull monitor · market data · event detector · scheduler ·
        │                  portfolio state · reconciliation · safety guard · observability)
        │  meaningful event
        ▼
Claude (portfolio manager) ─▶ opportunity discovery ─▶ DECISION
        │
        ├── WAIT ───────────────────────────▶ Decision Journal
        └── TRADE ─▶ Safety Guard ─▶ Webull ─▶ Execution ─▶ Result
                                                        │
              Decision Journal ◀── Counterfactuals ◀────┘
                        │
                        ▼
                 Learning Engine ─▶ Persistent Memory ─▶ (next) Claude
```

## Module map (`src/apm/*` ↔ spec §56-57)

| Module | Responsibility | Milestone |
|---|---|---|
| `config` | env-only settings, kill switch, execution mode | M0 |
| `observability` | structlog + secret redaction, metrics | M0 |
| `db` | async engine/session, ORM base, repositories | M0/M2 |
| `orchestrator` | entrypoint, health, migrations, event loop | M0 |
| `webull` | broker adapter (Protocol + Real + Mock) | M1 |
| `marketdata` | quotes, historical, point-in-time | M1/M2 |
| `portfolio` | state, snapshots, exposure, P&L, drawdown | M2 |
| `reconcile` | Webull = source of truth reconciliation | M2 |
| `journal` | decision/order/execution/trade (kept separate) | M3 |
| `memory` | versioned persistent knowledge + retrieval | M3/M4 |
| `decision` | ClaudeProvider, context builder, contract, validation | M4 |
| `discovery` | opportunity discovery signals (no candidate gate) | M5 |
| `events` | event detection + meaningful-event wake logic | M5 |
| `scheduler` | scheduled reviews (APScheduler) | M5 |
| `safety` | Safety Guard (independent, un-bypassable) | M6 |
| `execution` | order lifecycle via Safety Guard, idempotency | M7 |
| `learning` | trade/counterfactual eval, metrics | M8 |
| `research` | backtesting + research (never touches account) | M8 |

## Key invariants
- **Decision ≠ Order ≠ Execution ≠ Trade** — separate records (spec §18).
- **REAL only** — no paper trading; no `execution_type`/`PAPER` field (spec §6, §19).
- **Webull = source of truth** — reconcile before important real orders; block on mismatch (spec §31).
- **Safety Guard is un-bypassable** — Claude has no tool that skips it (spec §34, §38).
- **Secrets never in prompts/DB/logs** (spec §62-63).

## Data model (spec §54)
Tables added incrementally via Alembic: `portfolio`, `portfolio_snapshot`, `position`,
`order`, `execution`, `decision`, `decision_candidate`, `trade`, `trade_event`,
`strategy`, `strategy_version`, `experiment`, `experiment_observation`, `market_regime`,
`market_observation`, `memory`, `memory_revision`, `daily_review`, `weekly_review`,
`monthly_review`, `system_event`, `safety_event`, `counterfactual`.

# Data Model (The Database)

The database is Postgres. It is only a **copy** of the truth — for the real account,
**Webull is always the source of truth** and reconciliation fixes any difference.

All 22 tables are defined in `src/apm/db/models.py` and created by migrations in
`migrations/`.

---

## 1. The golden rule: four separate things

A common mistake is to squash everything into one "trade" record. We keep them apart:

```
DECISION   "what Claude chose"          (may create 0, 1, or many orders)
   │
   ▼
ORDER      "an instruction to the broker" (may fill in parts, or be rejected)
   │
   ▼
EXECUTION  "an actual fill"              (one order can have many fills)
   │
   ▼
TRADE      "a position from open to close, with the final profit/loss"
```

There is **no** "paper" or "simulated" flag anywhere — this system only records real trades.

---

## 2. How the tables connect

```
                         ┌───────────────┐
                         │   portfolio   │  (one identity, e.g. "main-portfolio")
                         └──────┬────────┘
        ┌───────────────┬───────┼───────────────┬──────────────┐
        ▼               ▼       ▼               ▼              ▼
 portfolio_snapshot  position  decision       trade        strategy_version
   (point-in-time)  (current)    │              │           experiment
                                 │              │           review
                 ┌───────────────┼─────────┐    │           market_regime
                 ▼               ▼         ▼    ▼
         decision_candidate  counterfactual  order_record ──▶ execution
                                                 │
                                                 ▼
                                            (fills recorded)
        trade ──▶ trade_event   (OPEN / CLOSE / … events)
        memory ──▶ memory_revision   (every change kept as a new version)
        experiment ──▶ experiment_observation
```

Arrows mean "has many". Example: one `portfolio` has many `decision`s; one `decision` has
many `decision_candidate`s; one `order_record` has many `execution`s.

---

## 3. What each table is for

### Identity & state
| Table | What it stores |
|-------|----------------|
| `portfolio` | The single portfolio identity (owner, broker, mode). |
| `portfolio_snapshot` | A photo of the account at a moment: value, cash, buying power, exposures. Has a version that goes up by 1 each time. |
| `position` | The current holdings (a copy of what Webull says right now). |

### Decisions
| Table | What it stores |
|-------|----------------|
| `decision` | Every meaningful choice Claude made — **including WAIT**. Holds the thesis, confidence, reasoning, and snapshots of the portfolio/market at that time. |
| `decision_candidate` | Each idea that was considered in a decision, and whether it was chosen or rejected (and why). |
| `counterfactual` | "What if we had taken this idea?" Seeded when the decision is made (with the price then), scored later. This is how we learn from WAIT. |

### Orders & trades (the four separate things)
| Table | What it stores |
|-------|----------------|
| `order_record` | An instruction sent to the broker. `client_order_id` is unique — this prevents duplicate orders. |
| `execution` | A fill against an order (price, quantity, fees, time). |
| `trade` | A position from entry to exit, with final profit/loss, return %, holding time, thesis. |
| `trade_event` | Lifecycle events for a trade (OPEN, CLOSE, …). |

### Memory (never overwritten)
| Table | What it stores |
|-------|----------------|
| `memory` | A piece of knowledge (by category + key), with its current text. |
| `memory_revision` | Every past version of that knowledge. We append, we never erase. The global "memory version" (M1, M2, …) is the count of revisions. |

### Research & strategy
| Table | What it stores |
|-------|----------------|
| `strategy_version` | A versioned strategy idea: hypothesis, evidence, sample size, performance, known failure modes, status (HYPOTHESIS → … → PROMOTED/FAILED/RETIRED). Versions are never overwritten. |
| `experiment` | A hypothesis being tested, with a result once evaluated. |
| `experiment_observation` | Data points collected during an experiment. |
| `review` | Daily / weekly / monthly review summaries + metrics. |
| `market_regime` | Observed market regime over time (e.g. calm/volatile). |
| `market_observation` | Saved market data points per symbol. |

### System & safety (the audit trail)
| Table | What it stores |
|-------|----------------|
| `system_event` | General log of important events (reconciliation, emergency stop, etc.). |
| `safety_event` | Every Safety Guard decision: allowed or blocked, and why. Full audit. |
| `system_flag` | Key/value runtime flags. Holds the database half of the **kill switch**. |

---

## 4. A few things to know

- **Times** are stored as `timestamptz` (timezone-aware, in UTC) everywhere.
- **IDs** are UUID strings (except `portfolio.id`, which is the portfolio name, and
  `system_flag.key`).
- **Flexible fields** (snapshots, payloads, evidence) are stored as JSON columns.
- **No secrets** are ever stored here (no API keys, passwords, or tokens) — those live only
  in environment variables and are hidden from logs.

---

## 5. How to look inside

```bash
docker compose --profile tools up -d adminer   # web UI at http://localhost:8081
# or:
docker compose exec db psql -U apm -d apm -c "\dt"        # list tables
docker compose exec db psql -U apm -d apm -c "SELECT decision_type, symbol, confidence FROM decision ORDER BY timestamp DESC LIMIT 10;"
docker compose exec db psql -U apm -d apm -c "SELECT allowed, violation, reason FROM safety_event ORDER BY created_at DESC LIMIT 10;"
```

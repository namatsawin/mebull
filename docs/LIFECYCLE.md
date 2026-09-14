# Lifecycle (Step by Step)

This doc shows exactly what happens, in order, at each stage. Read
[HOW_IT_WORKS.md](HOW_IT_WORKS.md) first for the big picture.

---

## 1. Startup (when the program boots)

File: `src/apm/orchestrator/main.py`

```
docker compose up
      │
      ▼
1. set up logging (JSON, secrets hidden)
2. run database migrations   (create/update tables)
3. wait until the database answers
4. build the TradingApp:
        adapter + portfolio + memory + journal + reconcile
        + review + event detector + Safety Guard + execution + engine + queue
5. app.start():
        - make sure the portfolio row exists
        - RECONCILE with Webull (our records = Webull's truth)
6. start the DECISION LOOP (runs a cycle now, then every N seconds)
7. start the HEALTH server (/health, /ready, /status, /metrics)
      │
      ▼
   now it runs a decision cycle every APM_DECISION_INTERVAL_SECONDS
      │
   (SIGTERM / Ctrl-C)
      ▼
8. shutdown: stop the loop → stop the app → stop health server
```

Health endpoints you can curl:

```
/health   → is the process alive?          {"status":"ok"}
/ready    → can it reach the database?      {"status":"ready","db":"up"}
/status   → safe snapshot (no secrets)      mode, trading on/off, watchlist
/metrics  → Prometheus metrics
```

---

## 2. The timer → a free decision

There is **no** meaningful-event gate. A single timer drives everything: every
`APM_DECISION_INTERVAL_SECONDS` the orchestrator runs one full decision cycle, and Claude
decides for itself what to do.

```
   ┌──────────────────────────────────────────────┐
   │  decision loop (src/apm/orchestrator/main.py) │
   │                                               │
   │   run one cycle now                           │
   │        │                                      │
   │        ▼                                      │
   │   app.run_once("PERIODIC")                    │
   │        │   → engine.run_cycle()  (see §3)     │
   │        │   → evaluate counterfactuals         │
   │        ▼                                      │
   │   wait N seconds (or wake on shutdown)        │
   │        │                                      │
   │        └────────── repeat ───────────────┐   │
   └──────────────────────────────────────────┼───┘
                                               ▼
```

Cycles run one after another (never overlapping): the loop always waits for a cycle to
finish before sleeping, so the AI only reasons about one thing at a time.

Set the interval in the environment:

```
APM_DECISION_INTERVAL_SECONDS=300   # every 5 minutes (default)
APM_DECISION_INTERVAL_SECONDS=60    # every minute (more reactive, more cost)
```

---

## 3. The decision cycle (the core loop)

File: `src/apm/decision/engine.py` → `run_cycle()`

```
1. make sure the portfolio exists
2. BUILD STATE  (portfolio/service.py)
     - ask Webull: cash, buying power, positions, open orders
     - compute exposures (long/short/gross/net)
     - SAVE a snapshot (version + 1) and the current positions
3. BUILD CONTEXT  (decision/context_builder.py)   ← a small, relevant package
     - quotes for: watchlist + current holdings
     - recent decisions (last 10)
     - relevant memories + known failures
     - (market discovery hook — not filled in yet)
4. ASK CLAUDE  (decision/provider.py)
     - Mock provider → returns WAIT (or a canned answer in tests)
     - Anthropic provider → calls the API and FORCES a structured JSON answer
5. VALIDATE the answer against the decision contract (decision/contract.py)
     - e.g. a BUY must have a symbol and quantity > 0
6. JOURNAL the decision  (journal/service.py)
     - save the Decision + every considered candidate + a counterfactual seed
7. IF the decision places an order AND an executor is set:
     → hand it to the Execution Coordinator (see section 4)
   ELSE:
     → done (this is the "analysis-only" case)
```

The context in step 3 is deliberately **small** — we never dump the whole database into the
prompt. This keeps the AI focused and the cost low.

---

## 4. Placing an order safely (the execution protocol)

Files: `src/apm/execution/coordinator.py` → `src/apm/execution/service.py`

The coordinator turns a Decision into an order request, then the service runs the careful
protocol. The **client_order_id** is built from the decision id, so a retry never creates a
duplicate (this is "idempotency").

```
Decision (BUY NVDA 10)
   │  coordinator: make OrderRequest, client_order_id = "apm-<decision_id>-0"
   ▼
ExecutionService.execute_order():

  1. RECONCILE            ── refresh our records from Webull first
  2. get account balance  ── how much buying power right now?
  3. PREVIEW the order    ── ask Webull for the estimated cost
  4. SAFETY GUARD ────────── the bouncer checks everything (see HOW_IT_WORKS §7)
        │                     │
        │ blocked             │ allowed
        ▼                     ▼
     stop, record        5. PLACE the order on Webull (idempotent)
     safety_event        6. save the order to our journal
                         7. MONITOR until it is done (filled/cancelled/…)
                         8. update the order in our journal
                         9. on a fill:
                              - save the execution (the fill)
                              - update the "trade book" (open/close a Trade, compute P&L)
                        10. RECONCILE again (records match Webull)
```

So a real order is always: **refresh → preview → guard → place → watch → record →
refresh**. The guard sits in the middle and cannot be skipped.

---

## 5. The timer

File: `src/apm/orchestrator/main.py` → `_decision_loop()`

There is just **one** knob: how often to run a decision cycle.

```
APM_DECISION_INTERVAL_SECONDS   (default 300 = every 5 minutes)
```

The loop runs a cycle immediately on startup, then fires on **wall-clock boundaries**
aligned to `interval` — with 300s that's :00 / :05 / :10 … (UTC), e.g. 20:35, 20:40, 20:45
— so cycles land on the clock instead of drifting by each cycle's runtime. If a cycle
overruns a boundary, the missed boundaries are coalesced (it targets the next one, never
runs back-to-back). Every cycle is a full, free decision (§3) plus a counterfactual update.
Lower the number to react faster (costs more tokens); raise it to save money.

> Note: this is a simple fixed interval — it does not know about market open/close hours or
> holidays. If you only want it active during market hours, gate it outside the app (e.g.
> start/stop the container on a schedule) or add a market-calendar check later.

---

## 6. Learning (what happens after decisions)

File: `src/apm/learning/`

```
COUNTERFACTUALS:
  For each idea we considered (even ones we skipped), we saved the price at that moment.
  Later, LearningService prices it again and records "what return would that have made?"
  → this is how we learn from WAIT decisions without any fake money.

METRICS (from closed trades):
  sample size, win rate, expectancy, average win/loss, payoff ratio,
  profit factor, total profit/loss.

REVIEWS (daily/weekly/monthly):
  evaluate counterfactuals + compute metrics + write a short summary to the `review` table.
```

---

## 7. The emergency stop (kill switch)

Two independent switches; if EITHER is off, no real orders happen:

```
1. Environment:  APM_TRADING_ENABLED=false     (needs a restart)
2. Database flag: flip it live, no restart:
       apm-killswitch status
       apm-killswitch on  --note "why"    ← blocks ALL new orders instantly
       apm-killswitch off
```

When stopped, the AI still thinks, researches, and journals — it just cannot place orders.
Claude has no way to flip these switches; only an operator can.

# How It Works (Overview)

This doc explains, in simple words, what the system is and how the pieces fit together.
For step-by-step flows see [LIFECYCLE.md](LIFECYCLE.md). For the database see
[DATA_MODEL.md](DATA_MODEL.md). For running it see [RUNBOOK.md](RUNBOOK.md).

---

## 1. What is this?

One AI (Claude) that manages **one** real stock portfolio on Webull, by itself.

You give it a portfolio (and maybe a watchlist). You do **not** tell it what to buy, when
to buy, how much, or which strategy. The AI decides — or it decides to **WAIT** and do
nothing. Every choice is saved so the system can learn from it later.

The most important rule of the whole system:

```
Claude decides WHAT should happen.
The infrastructure decides WHETHER it is SAFE to do.
Webull decides WHAT ACTUALLY happened.
The database REMEMBERS what happened.
The learning engine decides WHAT WAS LEARNED.
Claude uses that to make the NEXT decision.
```

So Claude is smart but not trusted with power. A separate **Safety Guard** can always say
"no", and Claude cannot turn it off.

---

## 2. The big picture

```
              YOU (owner)
                 │  (optional watchlist: "watch these symbols")
                 ▼
        ┌──────────────────────┐
        │  ALWAYS-ON PROGRAM    │   runs 24/7 in Docker
        │  (the orchestrator)   │
        │                       │
        │  • keeps state fresh  │
        │  • the Safety Guard   │
        │  • runs a timer       │
        └───────────┬───────────┘
                    │  every N seconds (APM_DECISION_INTERVAL_SECONDS)
                    ▼
                 ┌───────┐
                 │ CLAUDE│  "given all this, what should I do?"  ← decides freely
                 └───┬───┘
                     ▼
                 DECISION   (BUY / SELL / CLOSE / HOLD / WAIT / ...)
                     │
          ┌──────────┴──────────┐
          │                     │
        WAIT                  TRADE
          │                     │
          ▼                     ▼
      write to journal    SAFETY GUARD ──(blocked)──▶ write to journal, stop
                                │ (allowed)
                                ▼
                             WEBULL  ──▶ real order ──▶ result
                                │
                                ▼
                      journal + learning + memory
                                │
                                └────────▶ back to CLAUDE next time
```

Key idea: **the AI decides freely on a timer.** Every `APM_DECISION_INTERVAL_SECONDS`
(e.g. 300 = 5 minutes) the program runs one full decision cycle and Claude decides for
itself what to do — including doing nothing (`WAIT`). There is no filter deciding "is this
worth it?"; Claude is fully in charge of each cycle. Set the interval higher to save money,
lower to react faster.

---

## 3. The parts (and what each one does)

Every part lives in `src/apm/<name>/`.

| Part | Folder | Job (in one line) |
|------|--------|-------------------|
| **Config** | `config.py` | Read settings + secrets from the environment. Holds the kill switch flag. |
| **Broker adapter** | `webull/` | Talk to Webull (or a fake broker). Get account, positions, quotes; place orders. |
| **Portfolio** | `portfolio/` | Work out how much money/positions you have and save a snapshot. |
| **Reconcile** | `reconcile/` | Compare our records to Webull. Webull always wins. Fix differences. |
| **Journal** | `journal/` | Save every decision, order, fill, and trade. |
| **Memory** | `memory/` | Save lessons learned. Keep old versions, never erase. |
| **Decision engine** | `decision/` | Build the context, ask Claude, check the answer. |
| **Safety Guard** | `safety/` | The bouncer. Says yes/no to every order. Claude can't bypass it. |
| **Execution** | `execution/` | The careful steps to place a real order safely. |
| **Learning** | `learning/` | Score past decisions, work out "what if", compute stats. |
| **Research** | `research/` | Backtest ideas and version strategies. Never touches real money. |
| **Orchestrator** | `orchestrator/` | Starts everything and runs the interval decision loop. Health endpoints. |

---

## 4. The three "modes"

The system can run in three modes (set by `APM_EXECUTION_MODE`):

```
MOCK      → fake broker in memory. No real account. Great for testing. (default)
SANDBOX   → Webull's test environment. Real API, fake money.
REAL      → the live account. Real money.
```

Safety rule: a **REAL** order is only allowed if BOTH are true:
1. `APM_TRADING_ENABLED=true`, and
2. the kill switch is clear.

If either is off, no real orders happen — but the AI keeps thinking, researching, and
writing to the journal. So you can run the whole brain safely with trading turned off.

---

## 5. What can Claude decide?

Claude always returns **one** structured decision. The type is one of:

```
BUY        buy something
SELL       sell something
CLOSE      exit a position completely
HOLD       keep what we have, do nothing new
WAIT       no good opportunity right now — do nothing  ← this is normal and OK
REBALANCE  adjust the mix of positions
RESEARCH   go study something
EXPERIMENT try a new idea in a controlled way
```

`WAIT` is a **first-class** answer, not a failure. The system even records what Claude
*considered but rejected*, so later it can check: "was waiting the right call?"

---

## 6. Two quick examples

**Example A — the AI waits**

```
timer fires (every N seconds)
  → get money + positions + quotes for SPY/QQQ/NVDA/TSLA
  → ask Claude
  → Claude: "Market is choppy. NVDA, QQQ, SPY all weak setups. WAIT."
  → save the decision + the 3 rejected ideas (with the price at that moment)
  → NO order placed
Later: learning engine checks what those 3 would have done → evidence for next time
```

**Example B — the AI buys**

```
timer fires
  → get money + positions + quotes; Claude sees NVDA is up strongly
  → Claude: "BUY NVDA 10 shares, thesis = breakout, stop if it loses 148"
  → SAFETY GUARD checks: money ok? not a duplicate? state fresh? ... all yes → ALLOW
  → place order on Webull → filled at 150
  → journal: order + fill + a new OPEN trade
  → reconcile with Webull so our records match
```

If the Safety Guard had said "no" (say, not enough buying power), the decision is still
saved, but **no order is placed**.

---

## 7. The Safety Guard (why you can trust it)

Before any order, the guard runs these checks **in order** and stops at the first failure:

```
1. Emergency stop on?          → block everything (even SANDBOX)
2. REAL mode but trading off?  → block
3. Database / market data down?→ block (we don't trade blind)
4. Order malformed?            → block (missing symbol, limit with no price...)
5. Quantity crazy or ≤ 0?      → block
6. Our state too old?          → block (must be freshly reconciled)
7. Our records ≠ Webull?       → block until fixed
8. Same order already sent?    → block (idempotency, no duplicates)
9. Too many orders too fast?   → block (runaway protection)
10. Not enough buying power?    → block
```

It **fails closed**: if it cannot be sure something is safe, it says no. Every yes/no is
saved to the `safety_event` table so you can audit it.

There are two independent "off switches":
- `APM_TRADING_ENABLED=false` (environment), and
- a database flag you flip live with `apm-killswitch on`.

Claude cannot touch either one.

---

## 8. How it gets smarter over time

The system does **not** get better just by trading more. It gets better by collecting
evidence:

```
decision → what happened → compare vs what we skipped ("counterfactual")
        → compute stats → form a hypothesis → backtest it
        → save as a strategy version → use it in the next decision
```

Important honesty rule built into the design: **a good outcome is not the same as a good
decision** (you can win on a bad bet). So the system stores the reasoning, not just the
profit, and it will not promote a strategy on just 1–3 lucky trades.

---

## 9. What is real vs. still to prove

- **Runs today, fully tested:** the whole brain + safety + journaling + learning in `MOCK`
  mode (fake broker), 71 automated tests passing.
- **Written but not yet verified live:** the real Webull adapter and the real Claude API
  call. They need your credentials and a SANDBOX run to confirm the exact data fields.
  See [PHASE0_WEBULL_CHECKLIST.md](PHASE0_WEBULL_CHECKLIST.md).

> Reminder: "it runs safely and learns" is not the same as "it makes money". There is no
> guaranteed profit. Start with trading disabled.

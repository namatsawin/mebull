"""QuantRuleProvider — deterministic day-trade rules engine (B architecture, spec §14).

Implements the ClaudeProvider interface (``analyze(context) -> Decision``) but uses NO AI: it
computes the decision from the context's deterministic signals (technicals, breadth) under the
constraints of the current SupervisorPolicy. One decision per cycle, prioritized:

  1. EXIT  — flatten a held name that hit target/stop or lost its trend.
  2. ENTER — the strongest, non-extended momentum name with acceptable reward:risk, sized to
             per-trade risk and affordable buying power.
  3. WAIT  — otherwise (first-class; the engine never forces a trade, spec §15).

Stateless exits: target/stop are re-derived from live intraday support/resistance each cycle,
so no per-position plan state is needed. The end-of-day flatten (orchestrator) is the backstop.
"""

from __future__ import annotations

import math

from apm.config import get_settings
from apm.decision.context import DecisionContext
from apm.decision.contract import Decision, DecisionType, EntryPlan, ExitPlan
from apm.domain import InstrumentType, OrderType, Side, TimeInForce
from apm.observability import get_logger
from apm.strategy.options import parse_contract_symbol, select_call_contract
from apm.strategy.policy import SupervisorPolicy, load_active_policy

_OPTION_TYPES = {InstrumentType.CALL_OPTION.value, InstrumentType.PUT_OPTION.value}

log = get_logger("rules")

_LONG_REGIMES = {"risk_on", "risk_on_tilt"}


class QuantRuleProvider:
    """Deterministic decider. Same interface as the AI provider so it drops into the engine."""

    async def analyze(self, context: DecisionContext) -> Decision:
        settings = get_settings()
        policy = await load_active_policy()
        tech = {t.symbol.upper(): t for t in context.technicals}
        positions = context.portfolio_state.get("positions", []) or []

        # 1) EXITS first — manage what we hold (options by premium P&L, equities by levels).
        for pos in positions:
            if not pos.get("quantity"):
                continue
            if pos.get("instrument_type") in _OPTION_TYPES:
                dec = _option_exit(context, pos, policy, settings)
                if dec is not None:
                    return dec
                continue
            t = tech.get(str(pos["symbol"]).upper())
            if t is None:
                continue
            reason = _exit_reason(t)
            if reason:
                return _close(context, str(pos["symbol"]).upper(), reason, policy)

        # 2) ENTRIES — only if the supervisor permits and the regime corroborates longs.
        held = {str(p["symbol"]).upper(): p for p in positions}
        block = _entry_block_reason(context, policy, held)
        if block:
            return _wait(context, block, policy)

        candidate = _rank_entry(context, policy, held)
        if candidate is None:
            return _wait(context, "no momentum setup with acceptable reward:risk", policy)

        # Model B: on a small account, express the bullish setup as a defined-risk long CALL.
        if settings.options_enabled:
            dec = _option_entry(context, candidate, policy, settings)
            if dec is not None:
                return dec
            return _wait(context, f"{candidate.symbol}: no affordable/liquid ATM call", policy)

        qty, sizing_note = _size(context, candidate, policy)
        if qty < 1:
            return _wait(context, f"{candidate.symbol}: {sizing_note}", policy)

        return _buy(context, candidate, qty, policy, sizing_note)


# --- rule helpers -----------------------------------------------------------
def _exit_reason(t) -> str | None:
    px = t.last
    if t.resistance and px >= t.resistance * 0.999:
        return f"reached target/resistance {t.resistance}"
    if t.support and px <= t.support * 1.001:
        return f"hit stop/support {t.support}"
    if t.trend == "down":
        return "trend flipped down"
    return None


def _entry_block_reason(context, policy: SupervisorPolicy, held: dict) -> str | None:
    if not policy.trade_today:
        return f"supervisor paused new entries (regime={policy.regime})"
    if not policy.allow_longs:
        return "supervisor disallows longs"
    regime = (context.breadth or {}).get("regime", "")
    if regime not in _LONG_REGIMES:
        return f"breadth regime '{regime or 'unknown'}' does not corroborate longs"
    if len(held) >= policy.max_positions:
        return f"at max positions ({policy.max_positions})"
    if context.buying_power <= 0:
        return "no buying power"
    return None


def _reward_risk(t) -> float:
    reward = t.pct_to_resistance or 0.0
    risk = abs(t.pct_to_support or 0.0) or 0.1
    return reward / risk


def _rank_entry(context, policy: SupervisorPolicy, held: dict):
    veto = policy.normalized_veto()
    cands = [
        t
        for t in context.technicals
        if t.symbol.upper() not in held
        and t.symbol.upper() not in veto
        and t.trend == "up"
        and (t.momentum_5 or 0) >= policy.min_momentum_pct
        and (t.pct_to_resistance or 0) >= policy.min_room_to_target_pct
        and _reward_risk(t) >= policy.min_reward_risk
    ]
    if not cands:
        return None
    # Strongest intraday momentum wins.
    cands.sort(key=lambda t: (t.momentum_5 or 0), reverse=True)
    return cands[0]


def _size(context, t, policy: SupervisorPolicy) -> tuple[int, str]:
    settings = get_settings()
    nav = context.portfolio_state.get("portfolio_value") or context.buying_power
    risk_amt = nav * (settings.max_risk_per_trade_pct / 100.0) * policy.risk_multiplier
    stop_dist = max(0.01, t.last - (t.support or t.last * 0.99))
    by_risk = math.floor(risk_amt / stop_dist)
    by_bp = math.floor(context.buying_power / t.last) if t.last else 0
    qty = max(0, min(by_risk, by_bp))
    note = (
        f"risk ${risk_amt:.0f} / stop ${stop_dist:.2f} -> {by_risk} sh; "
        f"BP affords {by_bp} sh; take {qty}"
    )
    return qty, note


# --- decision builders ------------------------------------------------------
def _considered(context) -> list[str]:
    return [t.symbol for t in context.technicals] or context.watchlist


def _buy(context, t, qty: int, policy: SupervisorPolicy, sizing_note: str) -> Decision:
    rr = _reward_risk(t)
    return Decision(
        decision_type=DecisionType.BUY,
        portfolio_id=context.portfolio_id,
        symbol=t.symbol,
        instrument_type=InstrumentType.STOCK,
        action=Side.BUY,
        quantity=qty,
        thesis=(
            f"Momentum breakout: {t.symbol} trend up, momentum {t.momentum_5}% , "
            f"{t.pct_to_resistance}% to resistance {t.resistance}, R:R {rr:.1f}. "
            f"Regime {(context.breadth or {}).get('regime')}."
        ),
        entry_plan=EntryPlan(type=OrderType.LIMIT, price=t.last, time_in_force=TimeInForce.DAY),
        exit_plan=ExitPlan(
            type="CONDITIONAL",
            take_profit=t.resistance,
            stop_loss=t.support,
            note="Exit at resistance/support or trend flip; flatten before close regardless.",
        ),
        invalidation_condition=f"{t.symbol} loses support {t.support} or momentum rolls negative",
        expected_outcome=f"Move toward {t.resistance} for R:R ~{rr:.1f}",
        confidence=round(min(0.85, 0.55 + min(rr, 3) * 0.1), 2),
        reasoning_summary=(
            f"[QUANT] BUY {qty} {t.symbol} @ {t.last}. {sizing_note}. "
            f"Policy regime={policy.regime}, risk_mult={policy.risk_multiplier}."
        ),
        opportunities_considered=_considered(context),
        selected_opportunity=t.symbol,
    )


def _option_entry(context, t, policy: SupervisorPolicy, settings) -> Decision | None:
    """Model B: buy a defined-risk ATM-ish CALL on the bullish momentum name (1 contract)."""
    chain = (context.option_chains or {}).get(t.symbol.upper()) or []
    if not chain:
        return None
    nav = context.portfolio_state.get("portfolio_value") or context.buying_power
    max_cost = min(context.buying_power, nav * settings.option_max_premium_pct_nav / 100.0)
    c = select_call_contract(
        chain,
        max_cost=max_cost,
        delta_min=settings.option_delta_min,
        delta_max=settings.option_delta_max,
        max_spread_pct=settings.option_max_spread_pct,
    )
    if c is None:
        return None
    qty = settings.option_max_contracts
    tp = round((c["mid"] or c["ask"]) * (1 + settings.option_take_profit_pct / 100.0), 2)
    sl = round((c["mid"] or c["bid"]) * (1 - settings.option_stop_loss_pct / 100.0), 2)
    return Decision(
        decision_type=DecisionType.BUY,
        portfolio_id=context.portfolio_id,
        symbol=t.symbol,
        instrument_type=InstrumentType.CALL_OPTION,
        action=Side.BUY,
        quantity=qty,
        option_strike=c["strike"],
        option_expiry=c["expiry"],
        thesis=(
            f"Model B: {t.symbol} bullish (mom {t.momentum_5}% , trend up, regime "
            f"{(context.breadth or {}).get('regime')}). Long {c['strike']}C {c['expiry']} "
            f"delta {c.get('delta')}, IV {c.get('iv')}, cost ${c.get('cost')}."
        ),
        entry_plan=EntryPlan(type=OrderType.LIMIT, price=c["ask"], time_in_force=TimeInForce.DAY),
        exit_plan=ExitPlan(
            type="CONDITIONAL",
            take_profit=tp,
            stop_loss=sl,
            note=(
                f"+{settings.option_take_profit_pct}% TP / -{settings.option_stop_loss_pct}% SL "
                "on premium; flatten before close regardless (0DTE-safe)."
            ),
        ),
        invalidation_condition=f"premium -{settings.option_stop_loss_pct}% or underlying reverses",
        expected_outcome=f"premium +{settings.option_take_profit_pct}% on continuation",
        confidence=0.7,
        reasoning_summary=(
            f"[QUANT-B] BUY {qty} {t.symbol} {c['strike']}C {c['expiry']} @ {c['ask']} "
            f"(delta {c.get('delta')}, cost ${c.get('cost')}). Policy regime={policy.regime}."
        ),
        opportunities_considered=_considered(context),
        selected_opportunity=f"{t.symbol} {c['strike']}C",
    )


def _option_exit(context, pos: dict, policy: SupervisorPolicy, settings) -> Decision | None:
    """Sell a held option to close on premium TP/SL. Parses the contract to build the order."""
    avg = pos.get("avg_price")
    mark = pos.get("market_price")
    qty = abs(pos.get("quantity") or 0)
    if not avg or mark is None or qty < 1:
        return None  # can't evaluate P&L; EOD flatten is the backstop
    pnl_pct = (mark / avg - 1) * 100
    if pnl_pct < settings.option_take_profit_pct and pnl_pct > -settings.option_stop_loss_pct:
        return None  # still within the band — hold
    parsed = parse_contract_symbol(str(pos["symbol"]))
    if parsed is None:
        return None  # can't parse — EOD flatten will handle it
    tag = "take-profit" if pnl_pct >= settings.option_take_profit_pct else "stop-loss"
    return Decision(
        decision_type=DecisionType.SELL,
        portfolio_id=context.portfolio_id,
        symbol=parsed["underlying"],
        instrument_type=parsed["instrument_type"],
        action=Side.SELL,
        quantity=qty,
        option_strike=parsed["strike"],
        option_expiry=parsed["expiry"],
        thesis=f"Exit {pos['symbol']} ({tag}, premium {pnl_pct:+.0f}%).",
        confidence=0.7,
        reasoning_summary=f"[QUANT-B] SELL to close {pos['symbol']} — {tag} at {pnl_pct:+.0f}%.",
        opportunities_considered=_considered(context),
    )


def _close(context, symbol: str, reason: str, policy: SupervisorPolicy) -> Decision:
    return Decision(
        decision_type=DecisionType.CLOSE,
        portfolio_id=context.portfolio_id,
        symbol=symbol,
        instrument_type=InstrumentType.STOCK,
        thesis=f"Exit {symbol}: {reason}.",
        confidence=0.7,
        reasoning_summary=f"[QUANT] CLOSE {symbol}: {reason}.",
        opportunities_considered=_considered(context),
    )


def _wait(context, reason: str, policy: SupervisorPolicy) -> Decision:
    return Decision(
        decision_type=DecisionType.WAIT,
        portfolio_id=context.portfolio_id,
        confidence=0.6,
        reasoning_summary=f"[QUANT] WAIT: {reason}. (policy regime={policy.regime})",
        opportunities_considered=_considered(context),
    )

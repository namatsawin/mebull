"""SupervisorService — sets the trading policy the rules engine obeys (spec §14).

Two backends behind one interface:
  - MockSupervisor      : deterministic heuristic from breadth/regime (no network; tests/dev)
  - AnthropicSupervisor : one Claude call producing a validated SupervisorPolicy (a few/day)

The supervisor reads the same DecisionContext the engine builds, but only the high-level parts
(breadth, scorecard, positions). It NEVER places orders — it only writes policy.
"""

from __future__ import annotations

import json

from apm.config import get_settings
from apm.decision.context import DecisionContext
from apm.observability import get_logger
from apm.strategy.policy import SupervisorPolicy, save_policy

log = get_logger("supervisor")

_LONG_REGIMES = {"risk_on", "risk_on_tilt"}

_SYSTEM = """You are the risk/strategy supervisor for a deterministic intraday day-trading
engine. You do NOT place trades. A few times a day you set the POLICY the rules engine obeys:
whether to trade today, long/short permission, a per-trade risk multiplier, max concurrent
positions, entry-quality filters, and a veto list. Optimize for capital preservation and only
loosen risk when breadth/regime clearly corroborate. Be conservative in choppy/divergent or
risk-off tapes. Return exactly one policy via the submit_policy tool."""


def _heuristic_policy(context: DecisionContext) -> SupervisorPolicy:
    breadth = context.breadth or {}
    regime = breadth.get("regime", "unknown")
    avg = breadth.get("avg_change_pct", 0.0) or 0.0
    vix = context.vix
    trade = regime in _LONG_REGIMES
    if regime == "risk_on":
        risk_mult, max_pos = 1.0, 1
    elif regime == "risk_on_tilt":
        risk_mult, max_pos = 0.75, 1
    else:
        risk_mult, max_pos = 0.0, 1
    # VIX as a global risk multiplier: throttle back when volatility is elevated.
    vix_note = ""
    if vix is not None:
        if vix >= 30:
            risk_mult, trade = 0.0, False
            vix_note = f" VIX {vix} ≥30 → stand down (high-vol)."
        elif vix >= 22:
            risk_mult *= 0.5
            vix_note = f" VIX {vix} elevated → halve risk."
        else:
            vix_note = f" VIX {vix} calm."
    return SupervisorPolicy(
        regime=regime,
        trade_today=trade,
        allow_longs=True,
        allow_shorts=False,
        risk_multiplier=round(risk_mult, 2),
        max_positions=max_pos,
        rationale=(
            f"[heuristic] regime={regime}, avg_change={avg}%.{vix_note} "
            f"{'Longs enabled' if trade else 'Stand down.'}"
        ),
    )


class SupervisorService:
    def __init__(self) -> None:
        self._settings = get_settings()

    async def refresh(self, context: DecisionContext) -> SupervisorPolicy:
        try:
            if self._settings.claude_provider.value == "anthropic" and (
                self._settings.anthropic_api_key
                and self._settings.anthropic_api_key.get_secret_value()
            ):
                policy = await self._anthropic_policy(context)
            else:
                policy = _heuristic_policy(context)
        except Exception as exc:  # noqa: BLE001 - a bad supervisor call must fail safe
            log.warning("supervisor.failed", error=str(exc))
            policy = _heuristic_policy(context)
            policy.rationale = f"[fallback after error] {policy.rationale}"
        await save_policy(policy)
        return policy

    async def _anthropic_policy(self, context: DecisionContext) -> SupervisorPolicy:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=self._settings.anthropic_api_key.get_secret_value())
        schema = SupervisorPolicy.model_json_schema()
        brief = {
            "breadth": context.breadth,
            "vix": context.vix,
            "scorecard": context.scorecard,
            "positions": context.portfolio_state.get("positions", []),
            "buying_power": context.buying_power,
            "quotes": [q.model_dump() for q in context.quotes],
            "technicals": [t.model_dump() for t in context.technicals],
            "mandate": context.mandate,
        }
        resp = await client.messages.create(
            model=self._settings.claude_model,
            max_tokens=1024,
            system=_SYSTEM,
            tools=[
                {
                    "name": "submit_policy",
                    "description": "Submit the trading policy for the rules engine to obey.",
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": "submit_policy"},
            messages=[
                {
                    "role": "user",
                    "content": "Set today's policy from this snapshot.\n\n"
                    + json.dumps(brief, default=str),
                }
            ],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_policy":
                return SupervisorPolicy.model_validate(block.input)
        raise RuntimeError("supervisor response had no submit_policy tool call")

"""ClaudeProvider abstraction (spec §61).

The rest of the system does not care how Claude is invoked. Implementations:
  - MockProvider      — deterministic, no network; default for MOCK mode & tests
  - AnthropicProvider — Anthropic Messages API (pay-per-token) with tool-use forcing the
                        decision JSON schema. This is the sanctioned path for always-on
                        automation (no subscription/OAuth — avoids ban risk, spec §60).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from apm.config import get_settings
from apm.decision.context import DecisionContext
from apm.decision.contract import Decision, DecisionType, decision_json_schema
from apm.observability import get_logger

log = get_logger("claude")

_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[3] / "claude" / "system-prompt.md"


class ClaudeProvider(Protocol):
    async def analyze(self, context: DecisionContext) -> Decision: ...


def _load_system_prompt() -> str:
    try:
        return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return "You are the persistent portfolio manager. Optimize risk-adjusted quality or WAIT."


class MockProvider:
    """Deterministic provider. Defaults to WAIT (spec §15) — the safe, honest default when
    there is no real model. A canned decision or a rule callable can be injected for tests."""

    def __init__(
        self,
        canned: Decision | None = None,
        rule: Callable[[DecisionContext], Decision] | None = None,
    ) -> None:
        self._canned = canned
        self._rule = rule

    async def analyze(self, context: DecisionContext) -> Decision:
        if self._canned is not None:
            return self._canned.model_copy(deep=True)
        if self._rule is not None:
            return self._rule(context)
        considered = [q.symbol for q in context.quotes] or context.watchlist
        return Decision(
            decision_type=DecisionType.WAIT,
            portfolio_id=context.portfolio_id,
            confidence=0.6,
            reasoning_summary=(
                "No opportunity currently offers sufficient risk-adjusted edge; waiting."
            ),
            opportunities_considered=considered,
            rejected_opportunities=[
                {"symbol": s, "reason": "insufficient confirmation"} for s in considered
            ],
        )


class AnthropicProvider:
    """Uses the Anthropic Messages API with a forced ``submit_decision`` tool call."""

    _TOOL_NAME = "submit_decision"

    def __init__(self, api_key: str, model: str) -> None:
        self._model = model
        # Imported lazily so the core runs without the anthropic package configured.
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)
        self._system = _load_system_prompt()
        self._schema = decision_json_schema()

    async def analyze(self, context: DecisionContext) -> Decision:
        tools = [
            {
                "name": self._TOOL_NAME,
                "description": "Submit the single structured portfolio decision.",
                "input_schema": self._schema,
            }
        ]
        user_content = (
            "Decision context (JSON). Assess portfolio-level risk-adjusted opportunity, "
            "or WAIT. Return exactly one decision via the submit_decision tool.\n\n"
            + json.dumps(context.to_prompt_dict(), default=str)
        )
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=self._system,
            tools=tools,
            tool_choice={"type": "tool", "name": self._TOOL_NAME},
            messages=[{"role": "user", "content": user_content}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == self._TOOL_NAME:
                decision = Decision.model_validate(block.input)
                decision.portfolio_id = decision.portfolio_id or context.portfolio_id
                return decision
        raise RuntimeError("Anthropic response contained no submit_decision tool call")


def build_provider() -> ClaudeProvider:
    settings = get_settings()
    # Quant mode (B): a deterministic rules engine decides every cycle — no AI in the hot path.
    if settings.strategy_mode == "quant":
        from apm.strategy.rules import QuantRuleProvider

        return QuantRuleProvider()
    if settings.claude_provider.value == "anthropic":
        if not (settings.anthropic_api_key and settings.anthropic_api_key.get_secret_value()):
            raise RuntimeError("claude_provider=anthropic requires ANTHROPIC_API_KEY")
        return AnthropicProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.claude_model,
        )
    return MockProvider()

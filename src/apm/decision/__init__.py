"""Decision Engine (spec §14, §39-40, §45-46, §57, §61). Milestone M4.

ClaudeProvider abstraction (Anthropic API + Mock impls), context builder (relevance
retrieval, never a full DB dump), structured decision contract (pydantic -> JSON schema
via tool-use), decision validation, and session/identity management. WAIT is a
first-class decision (spec §15).
"""

from apm.decision.context import DecisionContext
from apm.decision.context_builder import ContextBuilder
from apm.decision.contract import Decision, DecisionType, decision_json_schema
from apm.decision.engine import DecisionEngine
from apm.decision.provider import (
    AnthropicProvider,
    ClaudeProvider,
    MockProvider,
    build_provider,
)

__all__ = [
    "Decision",
    "DecisionType",
    "decision_json_schema",
    "DecisionContext",
    "ContextBuilder",
    "DecisionEngine",
    "ClaudeProvider",
    "MockProvider",
    "AnthropicProvider",
    "build_provider",
]

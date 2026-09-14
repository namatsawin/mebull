"""Deterministic quant strategy (B architecture, spec §14).

The rules engine (``QuantRuleProvider``) decides every cycle with NO AI in the hot path,
reading a ``SupervisorPolicy`` the AI supervisor refreshes a few times a day. This keeps
execution reproducible, backtestable, and cheap; the AI is reserved for high-level judgment.
"""

from apm.strategy.policy import SupervisorPolicy, load_active_policy, save_policy

__all__ = ["SupervisorPolicy", "load_active_policy", "save_policy"]

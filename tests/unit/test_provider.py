import types

import pytest

from apm.decision.context import ContextQuote, DecisionContext
from apm.decision.contract import Decision, DecisionType
from apm.decision.provider import AnthropicProvider, MockProvider


def _ctx():
    return DecisionContext(
        trigger="TEST",
        portfolio_id="main-portfolio",
        as_of="2026-01-02T00:00:00+00:00",
        watchlist=["SPY", "NVDA"],
        quotes=[ContextQuote(symbol="NVDA", price=150.0)],
    )


async def test_mock_provider_defaults_to_wait():
    d = await MockProvider().analyze(_ctx())
    assert d.decision_type is DecisionType.WAIT
    assert "NVDA" in d.opportunities_considered


async def test_mock_provider_returns_canned_decision():
    canned = Decision(
        decision_type=DecisionType.BUY, symbol="NVDA", quantity=5,
        confidence=0.8, reasoning_summary="test",
    )
    d = await MockProvider(canned=canned).analyze(_ctx())
    assert d.decision_type is DecisionType.BUY
    assert d.symbol == "NVDA"


async def test_anthropic_provider_parses_tool_use(monkeypatch):
    provider = AnthropicProvider(api_key="sk-test", model="claude-opus-4-8")

    tool_block = types.SimpleNamespace(
        type="tool_use",
        name="submit_decision",
        input={
            "decision_type": "WAIT",
            "confidence": 0.65,
            "reasoning_summary": "no edge",
            "opportunities_considered": ["NVDA"],
        },
    )
    fake_resp = types.SimpleNamespace(content=[tool_block])

    async def fake_create(**kwargs):
        # Verify tool-use is forced (schema enforcement).
        assert kwargs["tool_choice"]["name"] == "submit_decision"
        return fake_resp

    monkeypatch.setattr(provider._client.messages, "create", fake_create)
    d = await provider.analyze(_ctx())
    assert d.decision_type is DecisionType.WAIT
    assert d.portfolio_id == "main-portfolio"


async def test_anthropic_provider_raises_without_tool_call(monkeypatch):
    provider = AnthropicProvider(api_key="sk-test", model="claude-opus-4-8")
    text_block = types.SimpleNamespace(type="text", text="hello")

    async def fake_create(**kwargs):
        return types.SimpleNamespace(content=[text_block])

    monkeypatch.setattr(provider._client.messages, "create", fake_create)
    with pytest.raises(RuntimeError):
        await provider.analyze(_ctx())

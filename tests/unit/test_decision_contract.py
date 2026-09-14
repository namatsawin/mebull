import pytest
from pydantic import ValidationError

from apm.decision.contract import Decision, DecisionType
from apm.domain import Side


def test_wait_is_valid_without_symbol():
    d = Decision(
        decision_type=DecisionType.WAIT,
        confidence=0.7,
        reasoning_summary="No sufficient edge right now.",
        opportunities_considered=["NVDA", "QQQ"],
        rejected_opportunities=[{"symbol": "NVDA", "reason": "insufficient confirmation"}],
    )
    assert d.decision_type is DecisionType.WAIT
    assert d.symbol is None


def test_buy_requires_symbol_and_quantity():
    with pytest.raises(ValidationError):
        Decision(decision_type=DecisionType.BUY, confidence=0.8, reasoning_summary="x")
    with pytest.raises(ValidationError):
        Decision(
            decision_type=DecisionType.BUY, symbol="NVDA", quantity=0,
            confidence=0.8, reasoning_summary="x",
        )


def test_buy_infers_action_side():
    d = Decision(
        decision_type=DecisionType.BUY, symbol="NVDA", quantity=10,
        confidence=0.8, reasoning_summary="momentum",
    )
    assert d.action is Side.BUY


def test_confidence_bounds_enforced():
    with pytest.raises(ValidationError):
        Decision(decision_type=DecisionType.HOLD, confidence=1.5, reasoning_summary="x")


def test_coerces_comma_separated_string_lists():
    # LLMs sometimes return list fields as a CSV string; the contract should accept both.
    d = Decision(
        decision_type=DecisionType.WAIT, confidence=0.7, reasoning_summary="x",
        opportunities_considered="SPY, QQQ, IWM",
        alternatives_considered="BUY IWM, WAIT",
    )
    assert d.opportunities_considered == ["SPY", "QQQ", "IWM"]
    assert d.alternatives_considered == ["BUY IWM", "WAIT"]


def test_places_order_flag():
    assert DecisionType.BUY.places_order
    assert DecisionType.CLOSE.places_order
    assert not DecisionType.WAIT.places_order
    assert not DecisionType.HOLD.places_order

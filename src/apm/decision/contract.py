"""The structured decision contract (spec §17, §40).

This pydantic model is the single source of truth for what Claude returns and what the
journal persists. At M4 it is converted to a JSON Schema and enforced via Anthropic
tool-use; validation here guarantees a decision is coherent before it can reach execution.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from apm.domain import InstrumentType, OrderType, Side, TimeInForce


def decision_json_schema() -> dict:
    """JSON Schema for the decision contract — used for Anthropic tool-use enforcement
    and written to claude/decision-schema.json."""
    return Decision.model_json_schema()


class DecisionType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    CLOSE = "CLOSE"
    HOLD = "HOLD"
    WAIT = "WAIT"
    REBALANCE = "REBALANCE"
    RESEARCH = "RESEARCH"
    EXPERIMENT = "EXPERIMENT"

    @property
    def places_order(self) -> bool:
        """Only these types can produce a broker order (spec §18)."""
        return self in {DecisionType.BUY, DecisionType.SELL, DecisionType.CLOSE}


class RejectedOpportunity(BaseModel):
    symbol: str
    reason: str
    price: float | None = None


class EntryPlan(BaseModel):
    type: OrderType = OrderType.LIMIT
    price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY


class ExitPlan(BaseModel):
    type: str = "CONDITIONAL"  # CONDITIONAL | LIMIT | STOP | TIME
    take_profit: float | None = None
    stop_loss: float | None = None
    note: str | None = None


class Decision(BaseModel):
    """A single structured decision. WAIT is first-class (spec §15)."""

    decision_type: DecisionType
    portfolio_id: str | None = None  # filled by the engine if the model omits it

    symbol: str | None = None
    instrument_type: InstrumentType | None = None
    action: Side | None = None
    quantity: float | None = None

    thesis: str | None = None
    entry_plan: EntryPlan | None = None
    exit_plan: ExitPlan | None = None
    invalidation_condition: str | None = None
    expected_outcome: str | None = None
    expected_holding_period: str | None = None

    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str

    opportunities_considered: list[str] = Field(default_factory=list)
    selected_opportunity: str | None = None
    rejected_opportunities: list[RejectedOpportunity] = Field(default_factory=list)
    alternatives_considered: list[str] = Field(default_factory=list)

    @field_validator(
        "opportunities_considered", "alternatives_considered", mode="before"
    )
    @classmethod
    def _coerce_str_list(cls, v):
        # LLMs sometimes return these as a comma-separated string instead of a JSON array,
        # even with tool-use schema enforcement — accept both.
        if v is None:
            return []
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @model_validator(mode="after")
    def _coherent(self) -> Decision:
        if self.decision_type.places_order:
            if not self.symbol:
                raise ValueError(f"{self.decision_type} requires a symbol")
            if self.decision_type in (DecisionType.BUY, DecisionType.SELL):
                if self.action is None:
                    self.action = (
                        Side.BUY if self.decision_type == DecisionType.BUY else Side.SELL
                    )
                if self.quantity is None or self.quantity <= 0:
                    raise ValueError(f"{self.decision_type} requires quantity > 0")
        return self

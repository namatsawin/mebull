"""DecisionContext — the relevant package assembled for Claude (spec §45-46).

Deliberately compact: portfolio state, watchlist quotes, discovery signals, recent
decisions, and the most relevant memories/failures — never a full DB dump. Serializes to
a plain dict for the prompt; contains no secrets (spec §62).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContextQuote(BaseModel):
    symbol: str
    price: float
    change_pct: float | None = None


class Technicals(BaseModel):
    """Deterministic technical context per symbol (spec §45-46) so the AI can anchor a
    risk-defined entry (support/stop) and read trend/volatility. Computed from historical
    bars — no extra LLM call."""

    symbol: str
    last: float
    sma20: float | None = None
    sma50: float | None = None
    support: float | None = None  # recent swing low — a natural stop anchor
    resistance: float | None = None  # recent swing high
    pct_to_support: float | None = None  # distance to stop, %
    pct_to_resistance: float | None = None  # room to target, %
    atr_pct: float | None = None  # avg daily range %, for stop sizing
    momentum_5: float | None = None  # % change over last 5 bars
    trend: str | None = None  # "up" | "down" | "flat"


class DecisionContext(BaseModel):
    trigger: str
    portfolio_id: str
    as_of: str

    portfolio_state: dict = Field(default_factory=dict)
    buying_power: float = 0.0

    watchlist: list[str] = Field(default_factory=list)
    quotes: list[ContextQuote] = Field(default_factory=list)
    # Deterministic technical levels per symbol (support/resistance/trend/vol) + a market
    # breadth summary — the risk-anchoring context a disciplined PM needs. No extra LLM call.
    technicals: list[Technicals] = Field(default_factory=list)
    breadth: dict = Field(default_factory=dict)
    # Affordable option contracts per underlying (only when options are enabled): compact
    # {symbol: [{right, strike, expiry, mid, cost}]} so the AI can pick a contract it can pay for.
    option_chains: dict[str, list[dict]] = Field(default_factory=dict)
    discovery: list[dict] = Field(default_factory=list)
    market_snapshot: dict = Field(default_factory=dict)

    recent_decisions: list[dict] = Field(default_factory=list)
    relevant_memories: list[dict] = Field(default_factory=list)
    known_failures: list[dict] = Field(default_factory=list)
    # Compact self-track-record (win-rate, calibration, what was rejected/waited on) so the
    # AI learns from its own history each cycle. Deterministic — no extra LLM call.
    scorecard: dict = Field(default_factory=dict)

    def candidate_prices(self) -> dict[str, float]:
        return {q.symbol: q.price for q in self.quotes}

    def to_prompt_dict(self) -> dict:
        return self.model_dump()

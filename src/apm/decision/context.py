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


class DecisionContext(BaseModel):
    trigger: str
    portfolio_id: str
    as_of: str

    portfolio_state: dict = Field(default_factory=dict)
    buying_power: float = 0.0

    watchlist: list[str] = Field(default_factory=list)
    quotes: list[ContextQuote] = Field(default_factory=list)
    discovery: list[dict] = Field(default_factory=list)
    market_snapshot: dict = Field(default_factory=dict)

    recent_decisions: list[dict] = Field(default_factory=list)
    relevant_memories: list[dict] = Field(default_factory=list)
    known_failures: list[dict] = Field(default_factory=list)

    def candidate_prices(self) -> dict[str, float]:
        return {q.symbol: q.price for q in self.quotes}

    def to_prompt_dict(self) -> dict:
        return self.model_dump()

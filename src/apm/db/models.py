"""ORM models (spec §54-55).

Kept in one module so Alembic autogenerate sees the full metadata. Design invariants:
- Decision ≠ Order ≠ Execution ≠ Trade — separate tables (spec §18).
- No PAPER / execution_type field — real trades only (spec §19).
- Every decision carries snapshots + memory/state versions (spec §17).

Research/strategy tables (strategy, experiment, reviews, market_regime) are added in a
later migration at M8 to keep this first schema focused on the execution-critical path.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apm.db.base import Base, TimestampMixin, new_uuid


class MemoryCategory(StrEnum):
    PORTFOLIO = "PORTFOLIO"
    MARKET = "MARKET"
    STRATEGY = "STRATEGY"
    TRADE = "TRADE"
    DECISION = "DECISION"
    EXPERIMENT = "EXPERIMENT"
    RESEARCH = "RESEARCH"
    FAILURE = "FAILURE"
    SUCCESS = "SUCCESS"


class TradeStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


# --- Portfolio (M2) ---------------------------------------------------------
class Portfolio(Base, TimestampMixin):
    __tablename__ = "portfolio"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # = portfolio_id
    owner: Mapped[str] = mapped_column(String(128))
    broker: Mapped[str] = mapped_column(String(32), default="Webull")
    execution_mode: Mapped[str] = mapped_column(String(16))

    snapshots: Mapped[list[PortfolioSnapshot]] = relationship(back_populates="portfolio")


class PortfolioSnapshot(Base):
    """Point-in-time portfolio state (spec §53). state_version increments per snapshot."""

    __tablename__ = "portfolio_snapshot"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolio.id"), index=True)
    as_of: Mapped[dt.datetime] = mapped_column(index=True)
    state_version: Mapped[int] = mapped_column(Integer)

    portfolio_value: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    buying_power: Mapped[float] = mapped_column(Float)
    gross_exposure: Mapped[float] = mapped_column(Float, default=0.0)
    net_exposure: Mapped[float] = mapped_column(Float, default=0.0)
    long_exposure: Mapped[float] = mapped_column(Float, default=0.0)
    short_exposure: Mapped[float] = mapped_column(Float, default=0.0)
    sector_exposure: Mapped[dict] = mapped_column(JSON, default=dict)
    asset_exposure: Mapped[dict] = mapped_column(JSON, default=dict)
    portfolio_volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
    open_orders: Mapped[int] = mapped_column(Integer, default=0)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))

    portfolio: Mapped[Portfolio] = relationship(back_populates="snapshots")


class PositionRecord(Base, TimestampMixin):
    """Latest known position per symbol (a representation; Webull is truth — spec §31)."""

    __tablename__ = "position"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolio.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    instrument_type: Mapped[str] = mapped_column(String(16), default="STOCK")
    quantity: Mapped[float] = mapped_column(Float)
    avg_price: Mapped[float] = mapped_column(Float)
    market_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    as_of: Mapped[dt.datetime] = mapped_column()


# --- Decision journal (M3) --------------------------------------------------
class Decision(Base):
    """A meaningful Claude decision, including WAIT (spec §16-17)."""

    __tablename__ = "decision"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolio.id"), index=True)
    timestamp: Mapped[dt.datetime] = mapped_column(index=True)
    decision_type: Mapped[str] = mapped_column(String(16), index=True)

    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    instrument_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    action: Mapped[str | None] = mapped_column(String(8), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)

    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    exit_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    invalidation_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_holding_period: Mapped[str | None] = mapped_column(String(64), nullable=True)

    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reasoning_summary: Mapped[str] = mapped_column(Text, default="")
    opportunities_considered: Mapped[list] = mapped_column(JSON, default=list)
    selected_opportunity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rejected_opportunities: Mapped[list] = mapped_column(JSON, default=list)
    alternatives_considered: Mapped[list] = mapped_column(JSON, default=list)

    portfolio_state_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    market_state_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    claude_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    memory_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    portfolio_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))

    candidates: Mapped[list[DecisionCandidate]] = relationship(back_populates="decision")


class DecisionCandidate(Base):
    """An opportunity considered by a decision (spec §20-21, §55)."""

    __tablename__ = "decision_candidate"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decision.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    chosen: Mapped[bool] = mapped_column(Boolean, default=False)
    reason_for_rejection: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_time_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))

    decision: Mapped[Decision] = relationship(back_populates="candidates")


class OrderRecord(Base):
    """Local record of a broker order (spec §18). client_order_id = idempotency key (§33)."""

    __tablename__ = "order_record"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_id: Mapped[str | None] = mapped_column(ForeignKey("decision.id"), nullable=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolio.id"), index=True)

    symbol: Mapped[str] = mapped_column(String(32), index=True)
    instrument_type: Mapped[str] = mapped_column(String(16), default="STOCK")
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[float] = mapped_column(Float)
    order_type: Mapped[str] = mapped_column(String(24), default="LIMIT")
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_in_force: Mapped[str] = mapped_column(String(8), default="DAY")

    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    filled_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    submitted_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    updated_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))

    executions: Mapped[list[Execution]] = relationship(back_populates="order")


class Execution(Base):
    """A fill against an order (spec §18)."""

    __tablename__ = "execution"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    order_id: Mapped[str] = mapped_column(ForeignKey("order_record.id"), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    executed_at: Mapped[dt.datetime] = mapped_column()
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))

    order: Mapped[OrderRecord] = relationship(back_populates="executions")


class Trade(Base):
    """A realized position lifecycle (spec §19). No PAPER/execution_type field."""

    __tablename__ = "trade"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    decision_id: Mapped[str | None] = mapped_column(ForeignKey("decision.id"), nullable=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolio.id"), index=True)

    symbol: Mapped[str] = mapped_column(String(32), index=True)
    instrument_type: Mapped[str] = mapped_column(String(16), default="STOCK")
    option_contract_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    strategy_version: Mapped[str | None] = mapped_column(String(16), nullable=True)

    entry_time: Mapped[dt.datetime] = mapped_column()
    entry_price: Mapped[float] = mapped_column(Float)
    exit_time: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float)
    capital_allocated: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_slippage: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_slippage: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    holding_period: Mapped[float | None] = mapped_column(Float, nullable=True)  # seconds
    mfe: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_regime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    volatility_regime: Mapped[str | None] = mapped_column(String(32), nullable=True)

    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalidation_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    claude_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_timestamp: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    memory_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    portfolio_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(8), default=TradeStatus.OPEN, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))

    events: Mapped[list[TradeEvent]] = relationship(back_populates="trade")


class TradeEvent(Base):
    __tablename__ = "trade_event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    trade_id: Mapped[str] = mapped_column(ForeignKey("trade.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[dt.datetime] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))

    trade: Mapped[Trade] = relationship(back_populates="events")


# --- Memory (M3/M4) ---------------------------------------------------------
class Memory(Base, TimestampMixin):
    """A knowledge item; content is versioned via MemoryRevision (spec §43-44)."""

    __tablename__ = "memory"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolio.id"), index=True)
    category: Mapped[str] = mapped_column(String(16), index=True)
    key: Mapped[str] = mapped_column(String(128), index=True)
    content: Mapped[str] = mapped_column(Text)
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    tags: Mapped[list] = mapped_column(JSON, default=list)

    revisions: Mapped[list[MemoryRevision]] = relationship(back_populates="memory")


class MemoryRevision(Base):
    __tablename__ = "memory_revision"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memory.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))

    memory: Mapped[Memory] = relationship(back_populates="revisions")


class Counterfactual(Base):
    """What a rejected candidate / WAIT would have done (spec §20-22)."""

    __tablename__ = "counterfactual"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decision.id"), index=True)
    candidate_symbol: Mapped[str] = mapped_column(String(32))
    candidate_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    chosen: Mapped[bool] = mapped_column(Boolean, default=False)
    decision_time_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    future_prices: Mapped[dict] = mapped_column(JSON, default=dict)  # horizon -> price
    counterfactual_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    counterfactual_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason_for_rejection: Mapped[str | None] = mapped_column(Text, nullable=True)
    evaluated_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))


# --- System / safety (M0/M6) ------------------------------------------------
class SystemEvent(Base):
    """Observability + audit trail (spec §58). Never contains secrets (§63)."""

    __tablename__ = "system_event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        default=lambda: dt.datetime.now(dt.UTC), index=True
    )


class SafetyEvent(Base):
    """Every Safety Guard verdict (spec §35, §58)."""

    __tablename__ = "safety_event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    decision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    client_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    allowed: Mapped[bool] = mapped_column(Boolean)
    violation: Mapped[str | None] = mapped_column(String(48), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        default=lambda: dt.datetime.now(dt.UTC), index=True
    )


class StrategyStatus(StrEnum):
    HYPOTHESIS = "HYPOTHESIS"
    RESEARCH = "RESEARCH"
    BACKTEST = "BACKTEST"
    EVALUATE = "EVALUATE"
    REAL_EXPERIMENT = "REAL_EXPERIMENT"
    PROMOTED = "PROMOTED"
    RETIRED = "RETIRED"
    FAILED = "FAILED"


class StrategyVersion(Base):
    """A versioned strategy hypothesis + its evidence (spec §27, §42). Never overwritten."""

    __tablename__ = "strategy_version"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolio.id"), index=True)
    # strategy_id is the name, e.g. "volatility_breakout".
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default=StrategyStatus.HYPOTHESIS, index=True)
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    implementation: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    performance: Mapped[dict] = mapped_column(JSON, default=dict)
    known_failure_modes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))
    retired_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


class Experiment(Base):
    """A strategy experiment (spec §26, §51). Research/backtest first, then optional real."""

    __tablename__ = "experiment"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolio.id"), index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="RUNNING", index=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))
    evaluated_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

    observations: Mapped[list[ExperimentObservation]] = relationship(back_populates="experiment")


class ExperimentObservation(Base):
    __tablename__ = "experiment_observation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiment.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))

    experiment: Mapped[Experiment] = relationship(back_populates="observations")


class Review(Base):
    """Daily/weekly/monthly review (spec §76-78). period distinguishes the cadence."""

    __tablename__ = "review"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolio.id"), index=True)
    period: Mapped[str] = mapped_column(String(12), index=True)  # DAILY | WEEKLY | MONTHLY
    as_of: Mapped[dt.datetime] = mapped_column(index=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))


class MarketRegime(Base):
    """Observed market regime over time (spec §54)."""

    __tablename__ = "market_regime"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    as_of: Mapped[dt.datetime] = mapped_column(index=True)
    regime: Mapped[str] = mapped_column(String(32))
    volatility_regime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))


class MarketObservation(Base):
    __tablename__ = "market_observation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    as_of: Mapped[dt.datetime] = mapped_column(index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.UTC))


class SystemFlag(Base, TimestampMixin):
    """Key/value runtime flags. Hosts the DB-backed half of the kill switch (spec §36)."""

    __tablename__ = "system_flag"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

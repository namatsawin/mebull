"""Decision Journal (spec §16-19).

Persists every meaningful decision — including WAIT — with the snapshots and versions
that make it reconstructable (spec §17). Seeds a counterfactual per considered candidate
so the learning engine can later evaluate what was rejected (spec §20-22). Also records
orders/executions/trades, keeping the four concepts as separate rows (spec §18).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from apm.config import get_settings
from apm.db import session_scope
from apm.db.models import (
    Counterfactual,
    DecisionCandidate,
    Execution,
    OrderRecord,
    TradeEvent,
)
from apm.db.models import (
    Decision as DecisionRow,
)
from apm.decision.contract import Decision
from apm.domain import BrokerOrder, OrderRequest
from apm.observability import get_logger
from apm.portfolio.state import PortfolioState

log = get_logger("journal")


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class JournalService:
    async def write_decision(
        self,
        decision: Decision,
        *,
        portfolio_state: PortfolioState,
        market_snapshot: dict | None = None,
        memory_version: str | None = None,
        session_id: str | None = None,
        candidate_prices: dict[str, float] | None = None,
    ) -> str:
        settings = get_settings()
        candidate_prices = candidate_prices or {}
        rejected = {r.symbol: r for r in decision.rejected_opportunities}
        ts = _utcnow()

        async with session_scope() as s:
            row = DecisionRow(
                portfolio_id=decision.portfolio_id or settings.portfolio_id,
                timestamp=ts,
                decision_type=decision.decision_type.value,
                symbol=decision.symbol,
                instrument_type=(
                    decision.instrument_type.value if decision.instrument_type else None
                ),
                action=decision.action.value if decision.action else None,
                quantity=decision.quantity,
                thesis=decision.thesis,
                entry_plan=decision.entry_plan.model_dump() if decision.entry_plan else None,
                exit_plan=decision.exit_plan.model_dump() if decision.exit_plan else None,
                invalidation_condition=decision.invalidation_condition,
                expected_outcome=decision.expected_outcome,
                expected_holding_period=decision.expected_holding_period,
                confidence=decision.confidence,
                reasoning_summary=decision.reasoning_summary,
                opportunities_considered=decision.opportunities_considered,
                selected_opportunity=decision.selected_opportunity,
                rejected_opportunities=[r.model_dump() for r in decision.rejected_opportunities],
                alternatives_considered=decision.alternatives_considered,
                portfolio_state_snapshot=portfolio_state.snapshot_summary(),
                market_state_snapshot=market_snapshot or {},
                claude_session_id=session_id,
                memory_version=memory_version,
                portfolio_state_version=portfolio_state.state_version,
            )
            s.add(row)
            await s.flush()  # get row.id

            for symbol in decision.opportunities_considered:
                chosen = symbol == decision.selected_opportunity
                price = candidate_prices.get(symbol)
                rej = rejected.get(symbol)
                s.add(
                    DecisionCandidate(
                        decision_id=row.id,
                        symbol=symbol,
                        action=decision.action.value if (chosen and decision.action) else None,
                        chosen=chosen,
                        reason_for_rejection=(rej.reason if rej else None),
                        decision_time_price=price,
                    )
                )
                # Seed a counterfactual for later evaluation (spec §20-22).
                s.add(
                    Counterfactual(
                        decision_id=row.id,
                        candidate_symbol=symbol,
                        candidate_action=decision.action.value if decision.action else None,
                        chosen=chosen,
                        decision_time_price=price,
                        reason_for_rejection=(rej.reason if rej else None),
                    )
                )
            decision_id = row.id

        log.info(
            "journal.decision",
            decision_id=decision_id,
            type=decision.decision_type.value,
            symbol=decision.symbol,
            confidence=decision.confidence,
            considered=len(decision.opportunities_considered),
        )
        return decision_id

    # --- order / execution lifecycle (used by execution service, M7) ---------
    async def upsert_order(
        self, order: BrokerOrder, request: OrderRequest, *, decision_id: str | None
    ) -> str:
        async with session_scope() as s:
            existing = await s.scalar(
                select(OrderRecord).where(
                    OrderRecord.client_order_id == order.client_order_id
                )
            )
            if existing is None:
                rec = OrderRecord(
                    client_order_id=order.client_order_id,
                    broker_order_id=order.broker_order_id,
                    decision_id=decision_id,
                    portfolio_id=get_settings().portfolio_id,
                    symbol=order.symbol,
                    instrument_type=request.instrument_type.value,
                    side=order.side.value,
                    quantity=order.quantity,
                    order_type=order.order_type.value,
                    limit_price=order.limit_price,
                    stop_price=request.stop_price,
                    time_in_force=request.time_in_force.value,
                    status=order.status.value,
                    filled_quantity=order.filled_quantity,
                    avg_fill_price=order.avg_fill_price,
                    submitted_at=_utcnow(),
                    updated_at=order.updated_at or _utcnow(),
                    raw=order.raw,
                )
                s.add(rec)
                await s.flush()
                oid = rec.id
            else:
                existing.broker_order_id = order.broker_order_id or existing.broker_order_id
                existing.status = order.status.value
                existing.filled_quantity = order.filled_quantity
                existing.avg_fill_price = order.avg_fill_price
                existing.updated_at = order.updated_at or _utcnow()
                oid = existing.id
        log.info("journal.order", client_order_id=order.client_order_id, status=order.status.value)
        return oid

    async def record_execution(
        self,
        order_db_id: str,
        *,
        quantity: float,
        price: float,
        fees: float = 0.0,
        executed_at: dt.datetime | None = None,
    ) -> None:
        async with session_scope() as s:
            s.add(
                Execution(
                    order_id=order_db_id,
                    quantity=quantity,
                    price=price,
                    fees=fees,
                    executed_at=executed_at or _utcnow(),
                )
            )

    async def record_trade_event(
        self, trade_id: str, event_type: str, payload: dict | None = None
    ) -> None:
        async with session_scope() as s:
            s.add(
                TradeEvent(
                    trade_id=trade_id,
                    event_type=event_type,
                    payload=payload or {},
                    occurred_at=_utcnow(),
                )
            )

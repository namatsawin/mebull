"""State Reconciliation (spec §31-32).

Webull is the source of truth. Reconciliation pulls broker truth, compares it against
the local representation, records any drift, and refreshes local state to match. The
Safety Guard (M6) consults ``last_reconciled_at`` / drift to block discretionary trades
on stale or mismatched state (spec §37).
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from apm.db import session_scope
from apm.db.models import SystemEvent
from apm.observability import get_logger
from apm.portfolio.service import PortfolioService
from apm.webull.adapter import WebullAdapter

log = get_logger("reconcile")


class PositionDrift(BaseModel):
    symbol: str
    local_qty: float
    broker_qty: float


class ReconResult(BaseModel):
    ok: bool
    as_of: dt.datetime
    cash_drift: float = 0.0
    position_drifts: list[PositionDrift] = Field(default_factory=list)
    order_drift: int = 0
    note: str = ""

    @property
    def has_drift(self) -> bool:
        return not self.ok


class ReconciliationService:
    def __init__(self, adapter: WebullAdapter, portfolio: PortfolioService) -> None:
        self._adapter = adapter
        self._portfolio = portfolio

    async def reconcile(self, *, reason: str = "scheduled") -> ReconResult:
        await self._adapter.authenticate()

        # Local representation BEFORE refresh.
        local = await self._portfolio.latest_persisted_positions()
        local_positions = {p.symbol: p.quantity for p in local}

        # Broker truth.
        broker_positions = await self._adapter.get_positions()
        broker_qty = {p.symbol: p.quantity for p in broker_positions}

        drifts: list[PositionDrift] = []
        for sym in set(local_positions) | set(broker_qty):
            lq = local_positions.get(sym, 0.0)
            bq = broker_qty.get(sym, 0.0)
            if abs(lq - bq) > 1e-9:
                drifts.append(PositionDrift(symbol=sym, local_qty=lq, broker_qty=bq))

        # Refresh local state to broker truth (this also persists a fresh snapshot).
        state = await self._portfolio.build_state(persist=True)

        ok = len(drifts) == 0
        result = ReconResult(
            ok=ok,
            as_of=state.as_of,
            position_drifts=drifts,
            order_drift=state.open_orders,
            note=reason if ok else f"{len(drifts)} position drift(s) corrected from broker truth",
        )

        async with session_scope() as s:
            s.add(
                SystemEvent(
                    event_type="reconciliation",
                    severity="info" if ok else "warning",
                    message=result.note,
                    payload={
                        "reason": reason,
                        "ok": ok,
                        "drifts": [d.model_dump() for d in drifts],
                        "state_version": state.state_version,
                    },
                )
            )
        log.info("reconcile.done", ok=ok, drifts=len(drifts), reason=reason)
        return result

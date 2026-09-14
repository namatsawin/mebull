"""Execution Service (spec §30, §57) — the real-order protocol.

Order of operations (spec §30):
  refresh/reconcile -> validate portfolio -> preview -> Safety Guard -> idempotency key ->
  submit -> monitor -> reconcile -> update local state -> journal.

MUST NOT bypass the Safety Guard (spec §57). Used in SANDBOX (M7) and REAL (M9); the mode
only changes which adapter is behind it and whether the guard permits REAL orders.
"""

from __future__ import annotations

import asyncio
import datetime as dt

from pydantic import BaseModel

from apm.domain import BrokerOrder, OrderRequest, OrderStatus
from apm.journal.service import JournalService
from apm.observability import get_logger
from apm.portfolio.service import PortfolioService
from apm.reconcile.service import ReconciliationService
from apm.safety.guard import Authorization, AuthorizationContext, SafetyGuard
from apm.webull.adapter import WebullAdapter

log = get_logger("execution")


class ExecutionResult(BaseModel):
    placed: bool
    authorization: Authorization
    order: BrokerOrder | None = None


class ExecutionService:
    def __init__(
        self,
        *,
        adapter: WebullAdapter,
        guard: SafetyGuard,
        journal: JournalService,
        portfolio: PortfolioService,
        reconcile: ReconciliationService,
        monitor_timeout: float = 30.0,
        poll_interval: float = 0.5,
    ) -> None:
        self._adapter = adapter
        self._guard = guard
        self._journal = journal
        self._portfolio = portfolio
        self._reconcile = reconcile
        self._monitor_timeout = monitor_timeout
        self._poll_interval = poll_interval

    async def execute_order(
        self, request: OrderRequest, *, decision_id: str | None = None
    ) -> ExecutionResult:
        # 1. Refresh state from broker truth before acting (spec §30-31).
        recon = await self._reconcile.reconcile(reason="pre-order")
        balance = await self._adapter.get_account_balance()

        # 2. Broker preview for cost/impact (spec §30). Options use the option endpoints.
        preview = await (
            self._adapter.preview_option_order(request)
            if request.is_option
            else self._adapter.preview_order(request)
        )

        # 3. Safety Guard — the un-bypassable gate (spec §34).
        ctx = AuthorizationContext(
            now=dt.datetime.now(dt.UTC),
            buying_power=balance.buying_power,
            existing_client_order_ids=await self._journal.existing_client_order_ids(),
            last_reconciled_at=recon.as_of,
            reconciled_ok=recon.ok,
            recent_order_count=await self._journal.recent_order_count(),
            estimated_cost=preview.estimated_cost,
            decision_id=decision_id,
        )
        auth = await self._guard.authorize(request, ctx)
        if not auth.allowed:
            log.warning("execution.blocked", reason=auth.reason, symbol=request.symbol)
            return ExecutionResult(placed=False, authorization=auth)

        # 4. Submit (idempotent by client_order_id, spec §33).
        order = await (
            self._adapter.place_option_order(request)
            if request.is_option
            else self._adapter.place_order(request)
        )
        order_db_id = await self._journal.upsert_order(order, request, decision_id=decision_id)

        # 5. Monitor to terminal/settled state (spec §30).
        order = await self._monitor(order)
        await self._journal.upsert_order(order, request, decision_id=decision_id)

        # 6. On fill: record execution + update the trade book.
        filled = order.status in (OrderStatus.FILLED, OrderStatus.PARTIAL_FILLED)
        if filled and order.filled_quantity > 0:
            fill_px = order.avg_fill_price or request.limit_price or 0.0
            await self._journal.record_execution(
                order_db_id, quantity=order.filled_quantity, price=fill_px
            )
            await self._journal.apply_fill(
                symbol=order.symbol,
                side=order.side.value,
                quantity=order.filled_quantity,
                price=fill_px,
                decision_id=decision_id,
            )

        # 7. Reconcile after the fill so local state matches broker truth (spec §32).
        await self._reconcile.reconcile(reason="post-order")

        log.info(
            "execution.done",
            client_order_id=order.client_order_id,
            status=order.status.value,
            filled=order.filled_quantity,
        )
        return ExecutionResult(placed=True, authorization=auth, order=order)

    async def _monitor(self, order: BrokerOrder) -> BrokerOrder:
        if order.status.is_terminal:
            return order
        deadline = asyncio.get_event_loop().time() + self._monitor_timeout
        while asyncio.get_event_loop().time() < deadline:
            latest = await self._adapter.get_order(order.client_order_id)
            if latest is not None:
                order = latest
                if order.status.is_terminal:
                    return order
            await asyncio.sleep(self._poll_interval)
        return order

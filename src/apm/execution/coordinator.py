"""ExecutionCoordinator — adapts a Claude Decision into a guarded order (spec §30, §40).

Bridges the DecisionEngine's ExecutionCoordinator protocol to the ExecutionService: turns
BUY/SELL/CLOSE decisions into OrderRequests (with a deterministic idempotency key from the
decision) and hands them to execution. WAIT/HOLD/RESEARCH never reach here.
"""

from __future__ import annotations

from apm.decision.context import DecisionContext
from apm.decision.contract import Decision, DecisionType
from apm.domain import InstrumentType, OptionRight, OrderRequest, OrderType, Side
from apm.execution.idempotency import make_client_order_id
from apm.execution.service import ExecutionResult, ExecutionService
from apm.observability import get_logger
from apm.portfolio.service import PortfolioService
from apm.webull.adapter import WebullAdapter

log = get_logger("coordinator")


class ExecutionCoordinator:
    def __init__(
        self,
        execution: ExecutionService,
        portfolio: PortfolioService,
        adapter: WebullAdapter,
    ) -> None:
        self._execution = execution
        self._portfolio = portfolio
        self._adapter = adapter

    async def execute(
        self, decision: Decision, *, context: DecisionContext, decision_id: str
    ) -> ExecutionResult | None:
        request = await self._to_order_request(decision, context, decision_id)
        if request is None:
            return None
        result = await self._execution.execute_order(request, decision_id=decision_id)
        log.info(
            "coordinator.result",
            decision_id=decision_id,
            placed=result.placed,
            allowed=result.authorization.allowed,
        )
        return result

    async def _to_order_request(
        self, decision: Decision, context: DecisionContext, decision_id: str
    ) -> OrderRequest | None:
        if not decision.decision_type.places_order or not decision.symbol:
            return None

        side, quantity = await self._resolve_side_quantity(decision)
        if quantity is None or quantity <= 0:
            log.warning("coordinator.no_quantity", decision_id=decision_id, symbol=decision.symbol)
            return None

        instrument = decision.instrument_type or InstrumentType.STOCK
        if instrument in (InstrumentType.CALL_OPTION, InstrumentType.PUT_OPTION):
            return await self._to_option_request(decision, side, quantity, decision_id)

        entry = decision.entry_plan
        order_type = entry.type if entry else OrderType.LIMIT
        limit_price = entry.price if entry else None
        # A limit order needs a price; fall back to the current quote from context.
        if order_type == OrderType.LIMIT and limit_price is None:
            limit_price = context.candidate_prices().get(decision.symbol)
            if limit_price is None:
                order_type = OrderType.MARKET

        return OrderRequest(
            client_order_id=make_client_order_id(decision_id),
            symbol=decision.symbol,
            instrument_type=instrument,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            time_in_force=(entry.time_in_force if entry else None) or _default_tif(),
            decision_id=decision_id,
        )

    async def _to_option_request(
        self, decision: Decision, side, quantity, decision_id: str
    ) -> OrderRequest | None:
        # Options are LIMIT-only; resolve the contract's price from the chain.
        right = (
            OptionRight.CALL
            if decision.instrument_type is InstrumentType.CALL_OPTION
            else OptionRight.PUT
        )
        chain = await self._adapter.get_option_chain(
            decision.symbol, expiry=decision.option_expiry, right=right
        )
        match = min(
            (c for c in chain if c.expiry == decision.option_expiry) or chain,
            key=lambda c: abs(c.strike - decision.option_strike),
            default=None,
        )
        if match is None or match.mid is None:
            log.warning(
                "coordinator.option_no_quote", decision_id=decision_id, symbol=decision.symbol
            )
            return None
        # Buy at the ask, sell at the bid (marketable limit); fall back to mid.
        limit = (match.ask if side == Side.BUY else match.bid) or match.mid
        return OrderRequest(
            client_order_id=make_client_order_id(decision_id),
            symbol=decision.symbol,
            instrument_type=decision.instrument_type,
            side=side,
            quantity=quantity,
            order_type=OrderType.LIMIT,
            limit_price=limit,
            option_strike=match.strike,
            option_expiry=match.expiry,
            option_contract_symbol=match.symbol,
            time_in_force=_default_tif(),
            decision_id=decision_id,
        )

    async def flatten_all(self, *, reason: str = "eod-flatten") -> int:
        """Deterministically close every open position (intraday-flat, spec §36 safety) — equities
        AND options (a held 0DTE must never reach expiry). Routes each close through the Safety
        Guard; never bypasses it. Returns close orders submitted."""
        positions = await self._portfolio.latest_persisted_positions()
        submitted = 0
        for pos in positions:
            if not pos.quantity:
                continue
            if pos.instrument_type in (InstrumentType.CALL_OPTION, InstrumentType.PUT_OPTION):
                request = await self._flatten_option_request(pos, reason)
                if request is None:
                    log.error("flatten.option_unresolved", symbol=pos.symbol, qty=pos.quantity)
                    continue
            else:
                side = Side.SELL if pos.quantity > 0 else Side.BUY
                request = OrderRequest(
                    client_order_id=make_client_order_id(f"{reason}:{pos.symbol}:{pos.quantity}"),
                    symbol=pos.symbol,
                    instrument_type=pos.instrument_type or InstrumentType.STOCK,
                    side=side,
                    quantity=abs(pos.quantity),
                    order_type=OrderType.MARKET,
                    time_in_force=_default_tif(),
                )
            result = await self._execution.execute_order(request, decision_id=None)
            if result.placed:
                submitted += 1
                log.info("flatten.closed", symbol=request.symbol, qty=request.quantity)
            else:
                log.warning(
                    "flatten.blocked", symbol=pos.symbol, reason=result.authorization.reason
                )
        return submitted

    async def _flatten_option_request(self, pos, reason: str) -> OrderRequest | None:
        """Build a marketable sell-to-close for a held option (LIMIT at current bid)."""
        from apm.strategy.options import parse_contract_symbol

        parsed = parse_contract_symbol(pos.symbol)
        if parsed is None or pos.quantity <= 0:
            return None  # short options / unparseable: escalate via the error log above
        chain = await self._adapter.get_option_chain(
            parsed["underlying"], expiry=parsed["expiry"], right=parsed["right"]
        )
        match = min(
            (c for c in chain if c.expiry == parsed["expiry"]) or chain,
            key=lambda c: abs(c.strike - parsed["strike"]),
            default=None,
        )
        limit = (match.bid if match else None) or (match.mid if match else None) or 0.01
        return OrderRequest(
            client_order_id=make_client_order_id(f"{reason}:{pos.symbol}:{pos.quantity}"),
            symbol=parsed["underlying"],
            instrument_type=parsed["instrument_type"],
            side=Side.SELL,
            quantity=abs(pos.quantity),
            order_type=OrderType.LIMIT,
            limit_price=round(limit, 2),
            option_strike=parsed["strike"],
            option_expiry=parsed["expiry"],
            option_contract_symbol=(match.symbol if match else pos.symbol),
            time_in_force=_default_tif(),
        )

    async def _resolve_side_quantity(self, decision: Decision):
        if decision.decision_type is DecisionType.CLOSE:
            # Close = flatten the current position in the symbol.
            positions = {p.symbol: p for p in await self._portfolio.latest_persisted_positions()}
            pos = positions.get(decision.symbol)
            if pos is None or pos.quantity == 0:
                return Side.SELL, 0.0
            side = Side.SELL if pos.quantity > 0 else Side.BUY
            return side, abs(pos.quantity)
        default_side = Side.BUY if decision.decision_type is DecisionType.BUY else Side.SELL
        return decision.action or default_side, decision.quantity


def _default_tif():
    from apm.domain import TimeInForce

    return TimeInForce.DAY

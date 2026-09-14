"""Safety Guard (spec §34-37, §79) — the un-bypassable execution gate.

Every order must pass ``authorize(...)`` before submission. This layer is independent of
Claude's reasoning: the decision layer has no path that skips it (spec §38). It fails
CLOSED — when a precondition cannot be verified, the order is blocked.

Every verdict (allow or block) is recorded as a SafetyEvent for audit (spec §58).
"""

from __future__ import annotations

import datetime as dt
import math
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from apm.config import ExecutionMode, Settings, get_settings
from apm.db import session_scope
from apm.db.models import SafetyEvent
from apm.domain import OrderRequest, OrderType, Side
from apm.observability import get_logger
from apm.safety.killswitch import KillSwitch

log = get_logger("safety")


class SafetyViolation(StrEnum):
    EMERGENCY_STOP = "EMERGENCY_STOP"
    TRADING_DISABLED = "TRADING_DISABLED"
    DATABASE_OUTAGE = "DATABASE_OUTAGE"
    MARKET_DATA_OUTAGE = "MARKET_DATA_OUTAGE"
    MALFORMED_ORDER = "MALFORMED_ORDER"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    STALE_STATE = "STALE_STATE"
    BROKER_MISMATCH = "BROKER_MISMATCH"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    RUNAWAY_FREQUENCY = "RUNAWAY_FREQUENCY"
    INSUFFICIENT_BUYING_POWER = "INSUFFICIENT_BUYING_POWER"


class Authorization(BaseModel):
    allowed: bool
    violation: SafetyViolation | None = None
    reason: str = ""


class AuthorizationContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    now: dt.datetime
    buying_power: float
    existing_client_order_ids: set[str] = Field(default_factory=set)
    last_reconciled_at: dt.datetime | None = None
    reconciled_ok: bool = True
    recent_order_count: int = 0
    market_data_ok: bool = True
    database_ok: bool = True
    estimated_cost: float | None = None
    decision_id: str | None = None


class SafetyGuard:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        killswitch: KillSwitch | None = None,
        max_staleness_seconds: float = 300.0,
        max_orders_per_window: int = 20,
        max_quantity: float = 100_000.0,
    ) -> None:
        self._settings = settings or get_settings()
        self._killswitch = killswitch or KillSwitch()
        self._max_staleness = max_staleness_seconds
        self._max_orders = max_orders_per_window
        self._max_quantity = max_quantity

    async def authorize(
        self, request: OrderRequest, ctx: AuthorizationContext
    ) -> Authorization:
        auth = await self._evaluate(request, ctx)
        await self._record(request, ctx, auth)
        level = "info" if auth.allowed else "warning"
        getattr(log, level)(
            "safety.verdict",
            allowed=auth.allowed,
            violation=auth.violation.value if auth.violation else None,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
        )
        return auth

    async def _evaluate(
        self, request: OrderRequest, ctx: AuthorizationContext
    ) -> Authorization:
        def block(v: SafetyViolation, reason: str) -> Authorization:
            return Authorization(allowed=False, violation=v, reason=reason)

        # 1. Emergency stop trumps everything (spec §36) — blocks even SANDBOX.
        if await self._killswitch.is_emergency_stopped():
            return block(SafetyViolation.EMERGENCY_STOP, "emergency stop engaged")

        # 2. Real orders require the env kill switch enabled + REAL mode (spec §36, §67).
        if self._settings.execution_mode is ExecutionMode.REAL:
            if not self._settings.trading_enabled:
                return block(
                    SafetyViolation.TRADING_DISABLED, "TRADING_ENABLED is false in REAL mode"
                )

        # 3. Infrastructure outages -> fail closed (spec §37).
        if not ctx.database_ok:
            return block(SafetyViolation.DATABASE_OUTAGE, "database unavailable")
        if not ctx.market_data_ok:
            return block(SafetyViolation.MARKET_DATA_OUTAGE, "market data unavailable")

        # 4. Well-formed order (spec §35).
        if not request.symbol or request.quantity is None:
            return block(SafetyViolation.MALFORMED_ORDER, "missing symbol or quantity")
        if request.side not in (Side.BUY, Side.SELL, Side.SHORT):
            return block(SafetyViolation.MALFORMED_ORDER, "invalid side")
        if request.order_type == OrderType.LIMIT and request.limit_price is None:
            return block(SafetyViolation.MALFORMED_ORDER, "limit order without limit price")
        if not math.isfinite(request.quantity) or request.quantity <= 0:
            return block(SafetyViolation.INVALID_QUANTITY, "quantity must be > 0 and finite")
        if request.quantity > self._max_quantity:
            return block(
                SafetyViolation.INVALID_QUANTITY,
                f"quantity {request.quantity} exceeds max {self._max_quantity}",
            )

        # 4b. Option-specific constraints (Webull): no MARKET, no SHORT, needs strike+expiry.
        if request.is_option:
            if request.order_type == OrderType.MARKET:
                return block(
                    SafetyViolation.MALFORMED_ORDER, "options do not support MARKET orders"
                )
            if request.side == Side.SHORT:
                return block(SafetyViolation.MALFORMED_ORDER, "options do not support SHORT")
            if request.option_strike is None or not request.option_expiry:
                return block(SafetyViolation.MALFORMED_ORDER, "option order missing strike/expiry")

        # 5. State freshness + broker agreement (spec §31-32, §37).
        if ctx.last_reconciled_at is None:
            return block(SafetyViolation.STALE_STATE, "no reconciliation on record")
        age = (ctx.now - ctx.last_reconciled_at).total_seconds()
        if age > self._max_staleness:
            return block(
                SafetyViolation.STALE_STATE,
                f"state stale by {age:.0f}s (> {self._max_staleness:.0f}s)",
            )
        if not ctx.reconciled_ok:
            return block(SafetyViolation.BROKER_MISMATCH, "unresolved local/broker mismatch")

        # 6. Idempotency / duplicate protection (spec §33).
        if request.client_order_id in ctx.existing_client_order_ids:
            return block(
                SafetyViolation.DUPLICATE_ORDER,
                f"client_order_id {request.client_order_id} already exists",
            )

        # 7. Runaway-loop protection (spec §35).
        if ctx.recent_order_count >= self._max_orders:
            return block(
                SafetyViolation.RUNAWAY_FREQUENCY,
                f"order frequency {ctx.recent_order_count} >= {self._max_orders} in window",
            )

        # 8. Buying power for buys (spec §35). Fail closed if cost can't be determined.
        if request.side == Side.BUY:
            cost = ctx.estimated_cost
            if cost is None and request.limit_price is not None:
                # Options carry a 100x contract multiplier.
                multiplier = 100 if request.is_option else 1
                cost = request.limit_price * request.quantity * multiplier
            if cost is None:
                return block(
                    SafetyViolation.INSUFFICIENT_BUYING_POWER,
                    "cannot determine cost for buying-power check",
                )
            if cost > ctx.buying_power:
                return block(
                    SafetyViolation.INSUFFICIENT_BUYING_POWER,
                    f"cost {cost:.2f} exceeds buying power {ctx.buying_power:.2f}",
                )

        return Authorization(allowed=True, reason="ok")

    async def _record(
        self, request: OrderRequest, ctx: AuthorizationContext, auth: Authorization
    ) -> None:
        try:
            async with session_scope() as s:
                s.add(
                    SafetyEvent(
                        decision_id=ctx.decision_id,
                        client_order_id=request.client_order_id,
                        allowed=auth.allowed,
                        violation=auth.violation.value if auth.violation else None,
                        reason=auth.reason,
                        payload={
                            "symbol": request.symbol,
                            "side": request.side.value,
                            "quantity": request.quantity,
                            "order_type": request.order_type.value,
                            "execution_mode": self._settings.execution_mode.value,
                        },
                    )
                )
        except Exception as exc:  # noqa: BLE001 - audit write must not mask the verdict
            log.error("safety.record_failed", error=str(exc))

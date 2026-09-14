"""Portfolio Service (spec §57) — builds and persists portfolio state from broker truth."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, func, select

from apm.config import get_settings
from apm.db import session_scope
from apm.db.models import Portfolio, PortfolioSnapshot, PositionRecord
from apm.domain import Position
from apm.observability import get_logger
from apm.portfolio.state import PortfolioState, compute_exposures
from apm.webull.adapter import WebullAdapter

log = get_logger("portfolio")


class PortfolioService:
    def __init__(self, adapter: WebullAdapter) -> None:
        self._adapter = adapter
        self._settings = get_settings()

    async def ensure_portfolio(self) -> None:
        """Create the persistent portfolio identity once (spec §5)."""
        async with session_scope() as s:
            existing = await s.get(Portfolio, self._settings.portfolio_id)
            if existing is None:
                s.add(
                    Portfolio(
                        id=self._settings.portfolio_id,
                        owner=self._settings.owner,
                        broker="Webull",
                        execution_mode=self._settings.execution_mode.value,
                    )
                )
                log.info("portfolio.created", portfolio_id=self._settings.portfolio_id)

    async def _next_state_version(self) -> int:
        async with session_scope() as s:
            current = await s.scalar(
                select(func.max(PortfolioSnapshot.state_version)).where(
                    PortfolioSnapshot.portfolio_id == self._settings.portfolio_id
                )
            )
        return (current or 0) + 1

    async def build_state(self, *, persist: bool = True) -> PortfolioState:
        """Fetch balance + positions from the broker and compute portfolio state.

        Broker is the source of truth (spec §31); this is the authoritative snapshot.
        """
        await self._adapter.authenticate()
        balance = await self._adapter.get_account_balance()
        positions = await self._adapter.get_positions()
        open_orders = await self._adapter.get_open_orders()

        exp = compute_exposures(positions)
        version = await self._next_state_version() if persist else 0
        state = PortfolioState(
            portfolio_id=self._settings.portfolio_id,
            as_of=balance.as_of,
            state_version=version,
            portfolio_value=balance.total_value,
            cash=balance.cash,
            buying_power=balance.buying_power,
            positions=positions,
            open_orders=len(open_orders),
            **exp,
        )
        if persist:
            await self._persist(state)
        return state

    async def _persist(self, state: PortfolioState) -> None:
        async with session_scope() as s:
            s.add(
                PortfolioSnapshot(
                    portfolio_id=state.portfolio_id,
                    as_of=state.as_of,
                    state_version=state.state_version,
                    portfolio_value=state.portfolio_value,
                    cash=state.cash,
                    buying_power=state.buying_power,
                    gross_exposure=state.gross_exposure,
                    net_exposure=state.net_exposure,
                    long_exposure=state.long_exposure,
                    short_exposure=state.short_exposure,
                    sector_exposure=state.sector_exposure,
                    asset_exposure=state.asset_exposure,
                    drawdown=state.drawdown,
                    open_positions=len(state.positions),
                    open_orders=state.open_orders,
                )
            )
            # Replace the position representation with current broker truth.
            await s.execute(
                delete(PositionRecord).where(
                    PositionRecord.portfolio_id == state.portfolio_id
                )
            )
            for p in state.positions:
                s.add(
                    PositionRecord(
                        portfolio_id=state.portfolio_id,
                        symbol=p.symbol,
                        instrument_type=p.instrument_type.value,
                        quantity=p.quantity,
                        avg_price=p.avg_price,
                        market_price=p.market_price,
                        as_of=state.as_of,
                    )
                )
        log.info(
            "portfolio.snapshot",
            state_version=state.state_version,
            value=round(state.portfolio_value, 2),
            positions=len(state.positions),
        )

    async def latest_persisted_positions(self) -> list[Position]:
        async with session_scope() as s:
            rows = (
                await s.scalars(
                    select(PositionRecord).where(
                        PositionRecord.portfolio_id == self._settings.portfolio_id
                    )
                )
            ).all()
        return [
            Position(
                symbol=r.symbol,
                quantity=r.quantity,
                avg_price=r.avg_price,
                market_price=r.market_price,
            )
            for r in rows
        ]

    async def last_snapshot_time(self) -> dt.datetime | None:
        async with session_scope() as s:
            return await s.scalar(
                select(func.max(PortfolioSnapshot.as_of)).where(
                    PortfolioSnapshot.portfolio_id == self._settings.portfolio_id
                )
            )

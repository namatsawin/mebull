"""Strategy lifecycle + versioning (spec §26-27, §42).

Strategies move HYPOTHESIS -> RESEARCH -> BACKTEST -> EVALUATE -> REAL_EXPERIMENT ->
PROMOTED (or FAILED/RETIRED). Versions are never overwritten (spec §27): each change is a
new StrategyVersion row carrying its own hypothesis, evidence, sample size, performance, and
known failure modes, so Claude can reason from which version actually worked.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from apm.config import get_settings
from apm.db import session_scope
from apm.db.models import StrategyStatus, StrategyVersion
from apm.observability import get_logger

log = get_logger("strategy")


class StrategyService:
    def __init__(self, portfolio_id: str | None = None) -> None:
        self._portfolio_id = portfolio_id or get_settings().portfolio_id

    async def create_version(
        self, strategy_id: str, *, hypothesis: str, implementation: str | None = None
    ) -> int:
        """Create the next version of a strategy (auto-incremented). Returns the version."""
        async with session_scope() as s:
            current = await s.scalar(
                select(func.max(StrategyVersion.version)).where(
                    StrategyVersion.portfolio_id == self._portfolio_id,
                    StrategyVersion.strategy_id == strategy_id,
                )
            )
            version = (current or 0) + 1
            s.add(
                StrategyVersion(
                    portfolio_id=self._portfolio_id,
                    strategy_id=strategy_id,
                    version=version,
                    status=StrategyStatus.HYPOTHESIS,
                    hypothesis=hypothesis,
                    implementation=implementation,
                )
            )
        log.info("strategy.version.created", strategy_id=strategy_id, version=version)
        return version

    async def record_evidence(
        self,
        strategy_id: str,
        version: int,
        *,
        evidence: dict,
        sample_size: int,
        performance: dict,
        known_failure_modes: str | None = None,
    ) -> None:
        async with session_scope() as s:
            row = await self._get(s, strategy_id, version)
            row.evidence = evidence
            row.sample_size = sample_size
            row.performance = performance
            if known_failure_modes is not None:
                row.known_failure_modes = known_failure_modes

    async def set_status(self, strategy_id: str, version: int, status: StrategyStatus) -> None:
        async with session_scope() as s:
            row = await self._get(s, strategy_id, version)
            row.status = status
            if status in (StrategyStatus.RETIRED, StrategyStatus.FAILED):
                row.retired_at = dt.datetime.now(dt.UTC)
        log.info("strategy.status", strategy_id=strategy_id, version=version, status=status)

    async def promoted(self, strategy_id: str) -> StrategyVersion | None:
        async with session_scope() as s:
            return await s.scalar(
                select(StrategyVersion)
                .where(
                    StrategyVersion.portfolio_id == self._portfolio_id,
                    StrategyVersion.strategy_id == strategy_id,
                    StrategyVersion.status == StrategyStatus.PROMOTED,
                )
                .order_by(StrategyVersion.version.desc())
            )

    async def _get(self, s, strategy_id: str, version: int) -> StrategyVersion:
        row = await s.scalar(
            select(StrategyVersion).where(
                StrategyVersion.portfolio_id == self._portfolio_id,
                StrategyVersion.strategy_id == strategy_id,
                StrategyVersion.version == version,
            )
        )
        if row is None:
            raise ValueError(f"strategy {strategy_id} v{version} not found")
        return row

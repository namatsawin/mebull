"""Experiment framework (spec §26, §51).

An experiment records a hypothesis, accumulates observations (from research/backtests), and
is evaluated to a result. A failed hypothesis is still useful knowledge (spec §26). Real
experiments only run subject to the Safety Guard (spec §51) — this module records intent and
evidence; it never places orders itself.
"""

from __future__ import annotations

import datetime as dt

from apm.config import get_settings
from apm.db import session_scope
from apm.db.models import Experiment, ExperimentObservation
from apm.observability import get_logger

log = get_logger("experiment")


class ExperimentService:
    def __init__(self, portfolio_id: str | None = None) -> None:
        self._portfolio_id = portfolio_id or get_settings().portfolio_id

    async def create(self, hypothesis: str, *, strategy_id: str | None = None) -> str:
        async with session_scope() as s:
            exp = Experiment(
                portfolio_id=self._portfolio_id,
                strategy_id=strategy_id,
                hypothesis=hypothesis,
                status="RUNNING",
            )
            s.add(exp)
            await s.flush()
            exp_id = exp.id
        log.info("experiment.created", experiment_id=exp_id)
        return exp_id

    async def observe(self, experiment_id: str, payload: dict) -> None:
        async with session_scope() as s:
            s.add(ExperimentObservation(experiment_id=experiment_id, payload=payload))

    async def evaluate(
        self, experiment_id: str, *, result: dict, status: str = "EVALUATED"
    ) -> None:
        async with session_scope() as s:
            exp = await s.get(Experiment, experiment_id)
            if exp is None:
                raise ValueError(f"experiment {experiment_id} not found")
            exp.result = result
            exp.status = status
            exp.evaluated_at = dt.datetime.now(dt.UTC)
        log.info("experiment.evaluated", experiment_id=experiment_id, status=status)

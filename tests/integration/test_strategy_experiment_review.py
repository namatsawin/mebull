import pytest
from sqlalchemy import func, select

from apm.db import session_scope
from apm.db.models import Review, StrategyStatus, StrategyVersion
from apm.learning.review import ReviewService
from apm.research.experiment import ExperimentService
from apm.research.strategy import StrategyService
from apm.webull.mock import MockWebullAdapter

pytestmark = pytest.mark.asyncio


async def test_strategy_versions_never_overwrite(clean_db):
    svc = StrategyService("main-portfolio")
    v1 = await svc.create_version("vol_breakout", hypothesis="vol expansion -> momentum")
    v2 = await svc.create_version("vol_breakout", hypothesis="refined: require volume")
    assert (v1, v2) == (1, 2)

    await svc.record_evidence(
        "vol_breakout", 2, evidence={"backtest": "ok"}, sample_size=120,
        performance={"expectancy": 0.4}, known_failure_modes="fails in high-vol earnings",
    )
    await svc.set_status("vol_breakout", 2, StrategyStatus.PROMOTED)

    promoted = await svc.promoted("vol_breakout")
    assert promoted.version == 2
    assert promoted.sample_size == 120

    async with session_scope() as s:
        n = await s.scalar(select(func.count()).select_from(StrategyVersion))
    assert n == 2  # both versions retained (spec §27)


async def test_experiment_lifecycle(clean_db):
    svc = ExperimentService("main-portfolio")
    exp_id = await svc.create("compression precedes momentum", strategy_id="vol_breakout")
    await svc.observe(exp_id, {"backtest_expectancy": 0.3})
    await svc.evaluate(exp_id, result={"decision": "promote"}, status="EVALUATED")

    from apm.db.models import Experiment, ExperimentObservation
    async with session_scope() as s:
        exp = await s.get(Experiment, exp_id)
        obs = await s.scalar(select(func.count()).select_from(ExperimentObservation))
    assert exp.status == "EVALUATED"
    assert exp.evaluated_at is not None
    assert obs == 1


async def test_review_generation_writes_row(clean_db):
    review_id = await ReviewService(MockWebullAdapter()).generate("DAILY")
    async with session_scope() as s:
        review = await s.get(Review, review_id)
    assert review.period == "DAILY"
    assert "review" in review.summary.lower()

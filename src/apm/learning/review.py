"""Periodic reviews (spec §76-78).

Daily/weekly/monthly reviews evaluate counterfactuals and compute performance metrics, then
persist a Review row. Triggered from the scheduler (spec §48) so the system continually turns
outcomes into evidence (spec §73).
"""

from __future__ import annotations

import datetime as dt

from apm.config import get_settings
from apm.db import session_scope
from apm.db.models import Review
from apm.learning.evaluator import LearningService
from apm.observability import get_logger
from apm.webull.adapter import WebullAdapter

log = get_logger("review")

# Map scheduled event names -> review period.
PERIOD_FOR_EVENT = {
    "END_OF_DAY_REVIEW": "DAILY",
    "WEEKLY_REVIEW": "WEEKLY",
    "MONTHLY_REVIEW": "MONTHLY",
}


class ReviewService:
    def __init__(self, adapter: WebullAdapter) -> None:
        self._learning = LearningService(adapter)

    async def generate(self, period: str) -> str:
        evaluated = await self._learning.evaluate_counterfactuals()
        metrics = await self._learning.compute_metrics()
        summary = self._summarize(period, metrics, evaluated)

        async with session_scope() as s:
            review = Review(
                portfolio_id=get_settings().portfolio_id,
                period=period,
                as_of=dt.datetime.now(dt.UTC),
                metrics={**metrics, "counterfactuals_evaluated": evaluated},
                summary=summary,
            )
            s.add(review)
            await s.flush()
            review_id = review.id
        log.info("review.generated", period=period, review_id=review_id)
        return review_id

    @staticmethod
    def _summarize(period: str, metrics: dict, evaluated: int) -> str:
        n = metrics.get("sample_size", 0)
        if n == 0:
            return f"{period} review: no closed trades yet; {evaluated} counterfactuals evaluated."
        return (
            f"{period} review: {n} closed trades, win rate "
            f"{metrics.get('win_rate', 0):.0%}, expectancy {metrics.get('expectancy', 0):.2f}, "
            f"total P&L {metrics.get('total_net_pnl', 0):.2f}; "
            f"{evaluated} counterfactuals evaluated."
        )

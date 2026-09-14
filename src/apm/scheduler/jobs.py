"""Scheduled review jobs (spec §48).

Registers cron jobs that push scheduled Events onto the orchestrator's queue, so every
reasoning path — scheduled or event-driven — flows through the same gate + engine.

Times are UTC and approximate US market hours; DST is not modeled here (a known
simplification — revisit with a market-calendar before REAL, docs/PHASE0).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from apm.events.detector import Event, EventType

SubmitFn = Callable[[Event], Awaitable[None]]


def register_review_jobs(scheduler: AsyncIOScheduler, submit: SubmitFn) -> None:
    def job(event_type: EventType):
        async def _run() -> None:
            await submit(Event(type=event_type))

        return _run

    # weekday = mon-fri
    specs = [
        (EventType.MARKET_OPEN_REVIEW, CronTrigger(day_of_week="mon-fri", hour=13, minute=35)),
        (EventType.MID_SESSION_REVIEW, CronTrigger(day_of_week="mon-fri", hour=17, minute=0)),
        (EventType.MARKET_CLOSE_REVIEW, CronTrigger(day_of_week="mon-fri", hour=19, minute=55)),
        (EventType.END_OF_DAY_REVIEW, CronTrigger(day_of_week="mon-fri", hour=21, minute=15)),
        (EventType.WEEKLY_REVIEW, CronTrigger(day_of_week="fri", hour=21, minute=30)),
        (EventType.MONTHLY_REVIEW, CronTrigger(day=1, hour=12, minute=0)),
    ]
    for event_type, trigger in specs:
        scheduler.add_job(job(event_type), trigger, id=event_type.value, replace_existing=True)

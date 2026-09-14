"""Process entrypoint — the always-on infrastructure (spec §11).

Startup (spec §49): configure logging -> migrate -> verify DB -> start TradingApp
(ensure portfolio, reconcile) -> run the decision loop every APM_DECISION_INTERVAL_SECONDS
-> serve health -> run until SIGTERM, then shut down gracefully.

The AI decides freely on every tick (no meaningful-event gate).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import signal
import time

import uvicorn

from apm import __version__
from apm.config import get_settings
from apm.db import ping
from apm.marketdata import minutes_to_close
from apm.observability import configure_logging, get_logger
from apm.orchestrator.app import TradingApp
from apm.orchestrator.health import create_health_app
from apm.orchestrator.migrate import run_migrations

log = get_logger("orchestrator")


async def _wait_for_db(retries: int = 30, delay: float = 2.0) -> None:
    for attempt in range(1, retries + 1):
        try:
            await ping()
            log.info("db.ready", attempt=attempt)
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("db.waiting", attempt=attempt, error=str(exc))
            await asyncio.sleep(delay)
    raise RuntimeError("database did not become ready in time")


def seconds_to_next_boundary(interval: float, now: float) -> float:
    """Seconds from `now` (epoch seconds) to the next wall-clock boundary aligned to
    `interval`. With interval=300 the boundaries land on :00/:05/:10 ... (UTC), so cycles
    fire on the clock (e.g. 20:35, 20:40) instead of drifting by each cycle's runtime.

    If `now` is exactly on a boundary, returns a full interval (wait for the next one).
    If a cycle overran one or more boundaries, this returns the time to the *next* one —
    missed boundaries are coalesced, never run back-to-back.
    """
    rem = now % interval
    return interval - rem if rem > 0 else interval


async def _decision_loop(
    app: TradingApp,
    stop: asyncio.Event,
    interval: float,
    *,
    market_hours_only: bool,
    intraday_only: bool,
    flatten_before_close_minutes: int,
) -> None:
    """Run one decision cycle on every wall-clock boundary until stopped.

    When ``market_hours_only`` is set, cycles are skipped while the US market is closed
    (weekends/holidays/after-hours) — the loop still wakes each boundary but does nothing,
    which costs no tokens.

    When ``intraday_only`` is set, the loop force-flattens all open positions once the session
    is within ``flatten_before_close_minutes`` of the close (deterministic intraday-flat
    guarantee — no position is ever held overnight, spec §36).
    """
    flattened_today = False
    while not stop.is_set():
        now = dt.datetime.now(dt.UTC)
        mins_left = minutes_to_close(now)
        in_flatten_window = (
            intraday_only and mins_left is not None and mins_left <= flatten_before_close_minutes
        )
        if market_hours_only and mins_left is None:
            log.info("loop.market_closed")
            flattened_today = False  # reset for the next session
        elif in_flatten_window:
            if not flattened_today:
                try:
                    count = await app.flatten_positions(reason="eod-flatten")
                    log.info("loop.eod_flatten", closed=count, minutes_to_close=mins_left)
                except Exception as exc:  # noqa: BLE001 - never kill the loop
                    log.error("loop.eod_flatten_failed", error=str(exc))
                flattened_today = True
            else:
                log.info("loop.eod_hold", minutes_to_close=mins_left)
        else:
            flattened_today = False
            try:
                decision = await app.run_once("PERIODIC")
                log.info("loop.cycle", decision_type=decision.decision_type.value)
            except Exception as exc:  # noqa: BLE001 - a bad cycle must not kill the loop
                log.error("loop.cycle_failed", error=str(exc))
        if stop.is_set():
            break
        # Sleep until the next clock-aligned boundary, but wake immediately on shutdown.
        sleep = seconds_to_next_boundary(interval, time.time())
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=sleep)


async def _serve_health(stop: asyncio.Event) -> None:
    s = get_settings()
    config = uvicorn.Config(
        create_health_app(),
        host="0.0.0.0",  # noqa: S104 - container-internal health surface
        port=s.health_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    await stop.wait()
    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError):
        await serve_task


async def async_main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json=settings.log_json)
    log.info("startup.begin", version=__version__, config=settings.redacted())

    await asyncio.to_thread(run_migrations)
    await _wait_for_db()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    app = TradingApp(settings)
    await app.start()

    health_task = asyncio.create_task(_serve_health(stop))
    loop_task = asyncio.create_task(
        _decision_loop(
            app,
            stop,
            settings.decision_interval_seconds,
            market_hours_only=settings.market_hours_only,
            intraday_only=settings.intraday_only,
            flatten_before_close_minutes=settings.flatten_before_close_minutes,
        )
    )
    log.info(
        "startup.complete",
        health_port=settings.health_port,
        interval_seconds=settings.decision_interval_seconds,
        market_hours_only=settings.market_hours_only,
        trading_style=settings.trading_style,
        intraday_only=settings.intraday_only,
    )

    await stop.wait()

    log.info("shutdown.begin")
    await loop_task
    await app.stop()
    await health_task
    log.info("shutdown.complete")


def run() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    run()

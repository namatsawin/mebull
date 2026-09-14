"""Process entrypoint — the always-on infrastructure (spec §11).

Startup (spec §49): configure logging -> migrate -> verify DB -> start TradingApp
(ensure portfolio, reconcile) -> run the decision loop every APM_DECISION_INTERVAL_SECONDS
-> serve health -> run until SIGTERM, then shut down gracefully.

The AI decides freely on every tick (no meaningful-event gate).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

import uvicorn

from apm import __version__
from apm.config import get_settings
from apm.db import ping
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


async def _decision_loop(app: TradingApp, stop: asyncio.Event, interval: float) -> None:
    """Run one decision cycle immediately, then every `interval` seconds until stopped."""
    while not stop.is_set():
        try:
            decision = await app.run_once("PERIODIC")
            log.info("loop.cycle", decision_type=decision.decision_type.value)
        except Exception as exc:  # noqa: BLE001 - a bad cycle must not kill the loop
            log.error("loop.cycle_failed", error=str(exc))
        # Sleep for `interval`, but wake immediately on shutdown.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


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
        _decision_loop(app, stop, settings.decision_interval_seconds)
    )
    log.info(
        "startup.complete",
        health_port=settings.health_port,
        interval_seconds=settings.decision_interval_seconds,
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

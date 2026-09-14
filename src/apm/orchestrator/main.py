"""Process entrypoint — the always-on infrastructure (spec §11).

Startup sequence (spec §49):
    configure logging -> run migrations -> verify DB -> [reconcile with Webull]*
    -> [load memory]* -> start health server -> start scheduler -> run event loop.

Steps marked * are wired in at later milestones (M1/M2 reconcile, M3/M4 memory).
For M0 this is a real long-lived asyncio process with a health endpoint and a
heartbeat, proving the Docker background operation works end to end.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from apm import __version__
from apm.config import get_settings
from apm.db import ping
from apm.observability import configure_logging, get_logger
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


async def _heartbeat() -> None:
    """Placeholder always-on tick. Replaced by the event/decision loop at M5."""
    s = get_settings()
    log.info(
        "heartbeat",
        portfolio_id=s.portfolio_id,
        execution_mode=s.execution_mode.value,
        trading_enabled=s.trading_enabled,
    )


def _build_scheduler() -> AsyncIOScheduler:
    """Scheduled reviews (spec §48). Jobs are added per milestone; M0 = heartbeat."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(_heartbeat, "interval", minutes=1, id="heartbeat")
    return scheduler


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

    # Migrations run in-process so `docker compose up` is fully self-contained.
    await asyncio.to_thread(run_migrations)
    await _wait_for_db()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    scheduler = _build_scheduler()
    scheduler.start()
    health_task = asyncio.create_task(_serve_health(stop))
    log.info("startup.complete", health_port=settings.health_port)

    await stop.wait()

    # Graceful shutdown (spec §11): stop scheduler, drain health server.
    log.info("shutdown.begin")
    scheduler.shutdown(wait=False)
    await health_task
    log.info("shutdown.complete")


def run() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    run()

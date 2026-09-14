"""Health + metrics HTTP surface for probes and observability (spec §58-59).

Deliberately tiny: liveness always OK once the process is up; readiness reflects
DB connectivity (the DB must be reachable for discretionary execution — spec §37).
"""

from __future__ import annotations

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from apm.config import get_settings
from apm.db import ping


def create_health_app() -> FastAPI:
    app = FastAPI(title="apm-health", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness: the process is running."""
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> Response:
        """Readiness: dependencies (DB) are reachable."""
        try:
            await ping()
        except Exception:  # noqa: BLE001 - report not-ready, never crash the probe
            return Response(
                content='{"status":"not-ready","db":"down"}',
                media_type="application/json",
                status_code=503,
            )
        return Response(
            content='{"status":"ready","db":"up"}',
            media_type="application/json",
            status_code=200,
        )

    @app.get("/status")
    async def status() -> dict[str, object]:
        """Non-secret operational snapshot (safe to expose)."""
        s = get_settings()
        return {
            "portfolio_id": s.portfolio_id,
            "execution_mode": s.execution_mode.value,
            "trading_enabled": s.trading_enabled,
            "can_place_real_orders": s.can_place_real_orders,
            "claude_provider": s.claude_provider.value,
            "watchlist": s.watchlist_symbols,
        }

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app

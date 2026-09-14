"""Adapter selection by execution mode (spec §67).

MOCK -> in-memory simulator. SANDBOX/REAL -> official SDK. The distinction between
SANDBOX and REAL is the credentials/endpoint configured in the Webull developer account;
the Safety Guard + kill switch (not the adapter) gate whether REAL orders may be placed.
"""

from __future__ import annotations

from apm.config import ExecutionMode, Settings, get_settings
from apm.webull.adapter import WebullAdapter
from apm.webull.mock import MockWebullAdapter


def build_adapter(settings: Settings | None = None) -> WebullAdapter:
    settings = settings or get_settings()

    if settings.execution_mode is ExecutionMode.MOCK:
        return MockWebullAdapter(account_id=f"MOCK-{settings.portfolio_id}")

    # SANDBOX / REAL both use the real SDK adapter.
    missing = [
        name
        for name, val in (
            ("WEBULL_APP_KEY", settings.webull_app_key),
            ("WEBULL_APP_SECRET", settings.webull_app_secret),
        )
        if not (val and val.get_secret_value())
    ]
    if missing:
        raise RuntimeError(
            f"execution_mode={settings.execution_mode} requires {', '.join(missing)}"
        )

    from apm.webull.real import RealWebullAdapter

    return RealWebullAdapter(
        app_key=settings.webull_app_key.get_secret_value(),  # type: ignore[union-attr]
        app_secret=settings.webull_app_secret.get_secret_value(),  # type: ignore[union-attr]
        region=settings.webull_region,
        account_id=settings.webull_account_id,
        market_category=settings.webull_market_category,
    )

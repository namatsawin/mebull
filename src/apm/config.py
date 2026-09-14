"""Central configuration — env only (spec §62-63: secrets never in prompts/DB/logs).

All settings load from environment variables prefixed with ``APM_`` (plus a few
third-party names like ``ANTHROPIC_API_KEY`` / ``WEBULL_*``). Nothing here is ever
logged directly; use ``settings.redacted()`` for safe display.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionMode(StrEnum):
    """How orders are handled. Real money only exists in REAL (spec §6, §67)."""

    MOCK = "MOCK"        # no broker; simulated adapter — analysis/dev
    SANDBOX = "SANDBOX"  # Webull sandbox — full order lifecycle, no real capital
    REAL = "REAL"        # live account — gated behind the Safety Guard + kill switch


class ClaudeProviderName(StrEnum):
    MOCK = "mock"
    ANTHROPIC = "anthropic"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Portfolio identity (spec §5) ---------------------------------------
    portfolio_id: str = "main-portfolio"
    owner: str = "owner"

    # --- Safety (spec §36) ---------------------------------------------------
    # Env half of the kill switch. The DB-backed half is checked at authorize-time.
    trading_enabled: bool = False
    execution_mode: ExecutionMode = ExecutionMode.MOCK

    # --- Decision cadence ----------------------------------------------------
    # The AI runs one full decision cycle every N seconds and decides freely each time
    # (no meaningful-event gate). Mind the Anthropic prompt-cache TTL / token cost.
    decision_interval_seconds: int = 300
    # Skip cycles when the US market is closed (weekends/holidays/after-hours). The biggest
    # cost saver. Set false for local dev/testing so the loop runs any time.
    market_hours_only: bool = True

    # --- Trading style / horizon --------------------------------------------
    # The trader's mandate, surfaced to the AI each cycle. "daytrade"/"scalp" bias toward
    # active intraday trading; "swing" allows multi-day holds. Informational for the model —
    # the hard intraday-flat guarantee below is enforced deterministically by the loop.
    trading_style: str = "daytrade"
    # Intraday-only: never hold overnight. The loop force-flattens all open positions once the
    # session is within `flatten_before_close_minutes` of the close (safe, deterministic).
    intraday_only: bool = True
    flatten_before_close_minutes: int = 15
    # Soft guidance to the model: risk at most this % of buying power on a single trade.
    max_risk_per_trade_pct: float = 2.0
    # Include an (affordable) option-chain slice per watchlist symbol in Claude's context so
    # it can trade options — needs the Webull OPRA option-data subscription. Off by default
    # (would 403 every cycle without OPRA).
    options_enabled: bool = False

    # --- Database ------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://apm:apm@localhost:5432/apm"

    # --- Claude --------------------------------------------------------------
    claude_provider: ClaudeProviderName = ClaudeProviderName.MOCK
    claude_model: str = "claude-opus-4-8"
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    # --- Webull (wired in at M1) --------------------------------------------
    webull_app_key: SecretStr | None = Field(default=None, alias="WEBULL_APP_KEY")
    webull_app_secret: SecretStr | None = Field(default=None, alias="WEBULL_APP_SECRET")
    webull_account_id: str | None = Field(default=None, alias="WEBULL_ACCOUNT_ID")
    webull_region: str = Field(default="us", alias="WEBULL_REGION")
    # Traded-market category for quotes/orders — the market of the SYMBOLS, not the account
    # region (a TH account trading SPY still uses US_STOCK). US_STOCK | US_ETF | HK_STOCK ...
    webull_market_category: str = Field(default="US_STOCK", alias="WEBULL_MARKET_CATEGORY")

    # --- Observability -------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True
    health_port: int = 8080

    # --- Watchlist (spec §4, §10) -------------------------------------------
    watchlist: str = ""

    @property
    def watchlist_symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.watchlist.split(",") if s.strip()]

    @property
    def can_place_real_orders(self) -> bool:
        """Env-level precondition for real orders. The Safety Guard enforces more."""
        return self.trading_enabled and self.execution_mode is ExecutionMode.REAL

    def redacted(self) -> dict[str, object]:
        """Config safe to log — secrets replaced with a marker."""
        def mark(v: SecretStr | None) -> str:
            return "***set***" if v and v.get_secret_value() else "***unset***"

        return {
            "portfolio_id": self.portfolio_id,
            "owner": self.owner,
            "trading_enabled": self.trading_enabled,
            "execution_mode": self.execution_mode.value,
            "decision_interval_seconds": self.decision_interval_seconds,
            "market_hours_only": self.market_hours_only,
            "options_enabled": self.options_enabled,
            "trading_style": self.trading_style,
            "intraday_only": self.intraday_only,
            "flatten_before_close_minutes": self.flatten_before_close_minutes,
            "max_risk_per_trade_pct": self.max_risk_per_trade_pct,
            "claude_provider": self.claude_provider.value,
            "claude_model": self.claude_model,
            "anthropic_api_key": mark(self.anthropic_api_key),
            "webull_app_key": mark(self.webull_app_key),
            "webull_app_secret": mark(self.webull_app_secret),
            "webull_account_id": self.webull_account_id or "***unset***",
            "webull_region": self.webull_region,
            "webull_market_category": self.webull_market_category,
            "database_url": _redact_url(self.database_url),
            "watchlist": self.watchlist_symbols,
            "log_level": self.log_level,
            "health_port": self.health_port,
        }


def _redact_url(url: str) -> str:
    """Hide credentials in a SQLAlchemy/DB URL for safe logging."""
    if "@" not in url:
        return url
    scheme_sep = "://"
    if scheme_sep not in url:
        return "***"
    scheme, rest = url.split(scheme_sep, 1)
    _, host = rest.rsplit("@", 1)
    return f"{scheme}{scheme_sep}***:***@{host}"


@lru_cache
def get_settings() -> Settings:
    return Settings()

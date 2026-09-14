from apm.config import ClaudeProviderName, ExecutionMode, Settings


def test_watchlist_parsing_normalizes_and_dedupes_whitespace():
    s = Settings(watchlist=" spy, qqq ,nvda,")
    assert s.watchlist_symbols == ["SPY", "QQQ", "NVDA"]


def test_can_place_real_orders_requires_flag_and_real_mode():
    def cfg(enabled, mode):
        return Settings(trading_enabled=enabled, execution_mode=mode).can_place_real_orders

    assert cfg(True, ExecutionMode.REAL)
    assert not cfg(False, ExecutionMode.REAL)
    assert not cfg(True, ExecutionMode.SANDBOX)


def test_defaults_are_safe():
    # Safety-first defaults: no trading, mock execution, mock provider (spec §36, §67).
    s = Settings()
    assert s.trading_enabled is False
    assert s.execution_mode is ExecutionMode.MOCK
    assert s.claude_provider is ClaudeProviderName.MOCK
    assert s.can_place_real_orders is False


def test_redacted_never_exposes_secrets():
    s = Settings(
        ANTHROPIC_API_KEY="sk-secret-value",
        WEBULL_APP_SECRET="webull-secret",
        database_url="postgresql+asyncpg://user:pass@db:5432/apm",
    )
    red = s.redacted()
    flat = str(red)
    assert "sk-secret-value" not in flat
    assert "webull-secret" not in flat
    assert "pass" not in flat
    assert red["anthropic_api_key"] == "***set***"
    assert red["webull_app_secret"] == "***set***"

import pytest

from apm.config import ExecutionMode, Settings
from apm.webull.factory import build_adapter
from apm.webull.mock import MockWebullAdapter


def test_mock_mode_returns_mock_adapter():
    adapter = build_adapter(Settings(execution_mode=ExecutionMode.MOCK))
    assert isinstance(adapter, MockWebullAdapter)


def test_sandbox_without_credentials_raises():
    with pytest.raises(RuntimeError, match="WEBULL_APP_KEY"):
        build_adapter(Settings(execution_mode=ExecutionMode.SANDBOX))


def test_real_without_credentials_raises():
    with pytest.raises(RuntimeError, match="WEBULL_APP_KEY"):
        build_adapter(Settings(execution_mode=ExecutionMode.REAL))


def test_real_with_credentials_builds_real_adapter():
    settings = Settings(
        execution_mode=ExecutionMode.REAL,
        WEBULL_APP_KEY="k",
        WEBULL_APP_SECRET="s",
        WEBULL_ACCOUNT_ID="acct",
    )
    adapter = build_adapter(settings)
    # Real adapter is constructed lazily; just assert it's not the mock.
    assert not isinstance(adapter, MockWebullAdapter)
    assert adapter.__class__.__name__ == "RealWebullAdapter"

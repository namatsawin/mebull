import datetime as dt

from apm.marketdata.hours import is_market_open


def _utc(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=dt.UTC)


def test_open_during_regular_session_winter_est():
    # 2026-01-02 is a Friday. EST = UTC-5, so 14:30 UTC = 09:30 ET (open bell).
    assert is_market_open(_utc(2026, 1, 2, 14, 30)) is True
    assert is_market_open(_utc(2026, 1, 2, 20, 0)) is True    # 15:00 ET, mid-session


def test_open_during_regular_session_summer_edt():
    # 2026-07-06 is a Monday. EDT = UTC-4, so 13:30 UTC = 09:30 ET (open bell).
    assert is_market_open(_utc(2026, 7, 6, 13, 30)) is True
    assert is_market_open(_utc(2026, 7, 6, 17, 0)) is True    # 13:00 ET, mid-session


def test_closed_before_open_and_after_close_est():
    assert is_market_open(_utc(2026, 1, 2, 14, 0)) is False   # 09:00 ET, before bell
    assert is_market_open(_utc(2026, 1, 2, 21, 0)) is False   # 16:00 ET, closing bell (exclusive)
    assert is_market_open(_utc(2026, 1, 2, 20, 59)) is True   # 15:59 ET, still open


def test_closed_on_weekend():
    # 2026-01-03 is a Saturday.
    assert is_market_open(_utc(2026, 1, 3, 15, 0)) is False
    # 2026-01-04 is a Sunday.
    assert is_market_open(_utc(2026, 1, 4, 15, 0)) is False


def test_closed_on_holidays():
    assert is_market_open(_utc(2026, 1, 1, 15, 0)) is False    # New Year's Day (Thu)
    assert is_market_open(_utc(2026, 7, 3, 15, 0)) is False    # Independence Day observed (Fri)
    assert is_market_open(_utc(2026, 12, 25, 15, 0)) is False  # Christmas (Fri)
    assert is_market_open(_utc(2026, 11, 26, 15, 0)) is False  # Thanksgiving (Thu)

"""US equity market hours (regular session), DST-aware.

Used by the orchestrator to skip decision cycles when the market is closed — the single
biggest cost saver, since a 24/7 loop otherwise burns ~65% of its tokens overnight and on
weekends when nothing trades.

Regular session only: 09:30–16:00 America/New_York, Mon–Fri, excluding full-day holidays.
Half-days (early 13:00 closes, e.g. day after Thanksgiving) are NOT modeled — they run the
full session, which is harmless (a few extra cycles), just not optimal.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_OPEN = dt.time(9, 30)
_CLOSE = dt.time(16, 0)

# NYSE/Nasdaq full-day holidays. Update annually (half-days not included).
_HOLIDAYS: set[dt.date] = {
    # 2026
    dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16), dt.date(2026, 4, 3),
    dt.date(2026, 5, 25), dt.date(2026, 6, 19), dt.date(2026, 7, 3), dt.date(2026, 9, 7),
    dt.date(2026, 11, 26), dt.date(2026, 12, 25),
    # 2027
    dt.date(2027, 1, 1), dt.date(2027, 1, 18), dt.date(2027, 2, 15), dt.date(2027, 3, 26),
    dt.date(2027, 5, 31), dt.date(2027, 6, 18), dt.date(2027, 7, 5), dt.date(2027, 9, 6),
    dt.date(2027, 11, 25), dt.date(2027, 12, 24),
}


def is_market_open(now_utc: dt.datetime) -> bool:
    """True if the US regular session is open at `now_utc` (a tz-aware UTC datetime)."""
    et = now_utc.astimezone(_ET)
    if et.weekday() >= 5:  # Saturday/Sunday
        return False
    if et.date() in _HOLIDAYS:
        return False
    return _OPEN <= et.time() < _CLOSE

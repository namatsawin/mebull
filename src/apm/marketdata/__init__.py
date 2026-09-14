"""Market Data Collector (spec §11, §57). Milestone M1/M2.

Real-time quotes (Webull MQTT), historical bars, and point-in-time data for research.
Stale market data must block new decisions (spec §37).
"""

from apm.marketdata.hours import is_market_open, minutes_to_close

__all__ = ["is_market_open", "minutes_to_close"]

"""Autonomous Market Discovery (spec §8-10). Milestone M5.

Organizes/ranks/indexes discovery signals (gainers, losers, unusual volume, volatility
expansion/compression, gaps, momentum, news, catalysts, ...). NO hard-coded candidate
gate (spec §9): infrastructure surfaces information; Claude decides what matters and may
discover opportunities outside the watchlist.
"""

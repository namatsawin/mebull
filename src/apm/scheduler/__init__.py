"""Scheduler (spec §48). Milestone M5.

Scheduled reviews: MARKET_OPEN / MID_SESSION / MARKET_CLOSE / END_OF_DAY / WEEKLY /
MONTHLY, plus EXPERIMENT / STRATEGY reviews and MEMORY_COMPRESSION. Backed by APScheduler
in the orchestrator.
"""

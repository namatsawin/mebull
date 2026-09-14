"""State Reconciliation (spec §32). Milestone M2.

Webull = source of truth (spec §31). Reconciles cash, buying power, positions, open
and filled orders on startup, on interval, after order events/fills/API errors, and
before important real orders. On mismatch: block new discretionary trades until resolved.
"""

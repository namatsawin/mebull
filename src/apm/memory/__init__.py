"""Persistent Memory (spec §43-44, §57). Milestone M3/M4.

Stores/retrieves/versions accumulated knowledge across categories (portfolio, market,
strategy, trade, decision, experiment, research, failure, success). Never silently
overwrites important memory — uses memory_revision (spec §44).
"""

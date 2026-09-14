"""Webull adapter (spec §57 "Webull Adapter"). Milestone M1.

Hides all Webull-specific details behind a ``WebullAdapter`` Protocol with Real
(official webull-openapi-python-sdk) and Mock implementations. Webull is the source
of truth for the account (spec §31).
"""

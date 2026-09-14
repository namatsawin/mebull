"""Webull adapter (spec §57 "Webull Adapter"). Milestone M1.

Hides all Webull-specific details behind a ``WebullAdapter`` Protocol with Real
(official webull-openapi-python-sdk) and Mock implementations. Webull is the source
of truth for the account (spec §31).
"""

from apm.webull.adapter import WebullAdapter
from apm.webull.factory import build_adapter
from apm.webull.mock import MockWebullAdapter

__all__ = ["WebullAdapter", "MockWebullAdapter", "build_adapter"]

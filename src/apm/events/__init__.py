"""Event Detector + wake logic (spec §12-13, §47). Milestone M5.

Local deterministic processing decides whether a market/portfolio/research event is
"meaningful" before waking Claude (spec §12) — controlling token cost (spec §60) and
avoiding overtrading.
"""

from apm.events.detector import Event, EventDetector, EventType

__all__ = ["Event", "EventDetector", "EventType"]

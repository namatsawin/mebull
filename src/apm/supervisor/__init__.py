"""AI supervisor (B architecture, spec §14).

Runs a few times a day (not every cycle) to set the SupervisorPolicy the deterministic rules
engine reads. This is the ONLY AI in quant mode — high-level judgment (regime, risk sizing,
vetoes), not per-trade execution.
"""

from apm.supervisor.service import SupervisorService

__all__ = ["SupervisorService"]

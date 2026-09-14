"""Safety Guard (spec §34-38, §79). Milestone M6. NON-NEGOTIABLE.

Infrastructure-level layer that Claude CANNOT disable, modify, or bypass. Every
execution call routes through ``SafetyGuard.authorize(...)``. Enforces the §35 checks,
the kill switch (spec §36), idempotency (spec §33), stale-state and broker-mismatch
blocking, and emergency stop.
"""

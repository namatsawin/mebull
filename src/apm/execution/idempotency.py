"""Idempotency keys (spec §33).

client_order_id is derived deterministically from the decision so a retry after a network
timeout reuses the same key — the broker (and the Safety Guard) treat it as the same order
rather than a duplicate.
"""

from __future__ import annotations


def make_client_order_id(decision_id: str, leg: int = 0) -> str:
    return f"apm-{decision_id}-{leg}"

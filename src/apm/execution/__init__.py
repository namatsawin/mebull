"""Execution Service (spec §30, §57). Milestone M7.

Order validation, preview, placement, cancellation, replacement, monitoring, and
execution reconciliation. Follows the real-trading protocol (spec §30) and MUST NOT
bypass the Safety Guard. Idempotency via client_order_id = fn(decision_id, leg) (spec §33).
"""

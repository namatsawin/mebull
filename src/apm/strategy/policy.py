"""SupervisorPolicy — the steering knobs the AI supervisor sets and the rules engine reads.

Deterministic persistence (spec §14): the supervisor writes a new active row a few times a
day; the rules engine loads the latest active policy each cycle (no AI). If none exists, a
safe default is used so the engine always has something to run on.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select, update

from apm.config import get_settings
from apm.db import session_scope
from apm.db.models import SupervisorPolicyRow
from apm.observability import get_logger

log = get_logger("policy")


class SupervisorPolicy(BaseModel):
    """High-level trading policy. The rules engine treats these as hard constraints."""

    regime: str = "unknown"  # risk_on | risk_on_tilt | mixed | risk_off_tilt | risk_off | high_vol
    trade_today: bool = True  # supervisor can pause all new entries
    allow_longs: bool = True
    allow_shorts: bool = False  # cash account: no shorts
    risk_multiplier: float = Field(default=1.0, ge=0.0, le=2.0)  # scales per-trade size
    max_positions: int = Field(default=1, ge=0)
    min_room_to_target_pct: float = 0.4  # entry filter: room to resistance (target)
    min_reward_risk: float = 1.3  # required reward:risk to enter
    min_momentum_pct: float = 0.1  # entry filter: intraday momentum
    veto_symbols: list[str] = Field(default_factory=list)
    rationale: str = ""

    def normalized_veto(self) -> set[str]:
        return {s.strip().upper() for s in self.veto_symbols if s.strip()}


def _default_policy() -> SupervisorPolicy:
    """Conservative default used before the supervisor has run (spec: fail safe)."""
    return SupervisorPolicy(rationale="default policy (supervisor has not run yet)")


async def load_active_policy() -> SupervisorPolicy:
    portfolio_id = get_settings().portfolio_id
    try:
        async with session_scope() as s:
            row = await s.scalar(
                select(SupervisorPolicyRow)
                .where(
                    SupervisorPolicyRow.portfolio_id == portfolio_id,
                    SupervisorPolicyRow.active.is_(True),
                )
                .order_by(SupervisorPolicyRow.created_at.desc())
                .limit(1)
            )
        if row is None or not row.payload:
            return _default_policy()
        return SupervisorPolicy.model_validate(row.payload)
    except Exception as exc:  # noqa: BLE001 - never let policy loading break trading
        log.warning("policy.load_failed", error=str(exc))
        return _default_policy()


async def save_policy(policy: SupervisorPolicy) -> str:
    """Persist a new active policy and deactivate the previous ones."""
    portfolio_id = get_settings().portfolio_id
    async with session_scope() as s:
        await s.execute(
            update(SupervisorPolicyRow)
            .where(
                SupervisorPolicyRow.portfolio_id == portfolio_id,
                SupervisorPolicyRow.active.is_(True),
            )
            .values(active=False)
        )
        row = SupervisorPolicyRow(
            portfolio_id=portfolio_id,
            active=True,
            regime=policy.regime,
            trade_today=policy.trade_today,
            risk_multiplier=policy.risk_multiplier,
            rationale=policy.rationale,
            payload=policy.model_dump(),
        )
        s.add(row)
        await s.flush()
        pid = row.id
    log.info(
        "policy.saved",
        policy_id=pid,
        regime=policy.regime,
        trade_today=policy.trade_today,
        risk_multiplier=policy.risk_multiplier,
    )
    return pid

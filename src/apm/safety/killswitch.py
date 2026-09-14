"""Kill switch — the DB-backed half of the emergency stop (spec §36).

Two independent halves; either one off ⇒ no real orders:
  1. env ``APM_TRADING_ENABLED`` (checked in the Safety Guard)
  2. this DB flag (an operator can trip it at runtime without a redeploy)

Claude cannot flip this — it is infrastructure the decision layer never touches (spec §38).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apm.db import session_scope
from apm.db.models import SystemEvent, SystemFlag
from apm.observability import get_logger

log = get_logger("killswitch")

_EMERGENCY_STOP_KEY = "emergency_stop"


class KillSwitch:
    async def is_emergency_stopped(self) -> bool:
        async with session_scope() as s:
            flag = await s.scalar(
                select(SystemFlag).where(SystemFlag.key == _EMERGENCY_STOP_KEY)
            )
            return bool(flag and flag.value == "true")

    async def set_emergency_stop(self, on: bool, *, note: str | None = None) -> None:
        value = "true" if on else "false"
        async with session_scope() as s:
            stmt = (
                pg_insert(SystemFlag)
                .values(key=_EMERGENCY_STOP_KEY, value=value, note=note)
                .on_conflict_do_update(
                    index_elements=[SystemFlag.key],
                    set_={"value": value, "note": note},
                )
            )
            await s.execute(stmt)
            s.add(
                SystemEvent(
                    event_type="emergency_stop",
                    severity="critical" if on else "info",
                    message=f"emergency_stop set to {on}",
                    payload={"note": note},
                )
            )
        log.warning("killswitch.set", emergency_stop=on, note=note)

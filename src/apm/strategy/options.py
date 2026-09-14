"""Option helpers for the deterministic Model B rules (spec §14).

Pure functions: parse a held contract symbol back to (underlying, right, strike, expiry) so we
can build a safe sell-to-close order, and select an entry contract (ATM-ish, affordable, liquid)
from the enriched option-chain slice in the context.
"""

from __future__ import annotations

import re

from apm.domain import InstrumentType, OptionRight

# OCC-style: ROOT + YYMMDD + C/P + strike*1000 (8 digits), e.g. AMD260914C00495000
_OCC = re.compile(r"^([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def parse_contract_symbol(sym: str) -> dict | None:
    """Parse a contract symbol (OCC or mock 'AMD 2026-09-14 C 495.0') to its parts."""
    if not sym:
        return None
    s = sym.strip().upper()
    # Mock format: "AMD 2026-09-14 C 495.0"
    if " " in s:
        parts = s.split()
        if len(parts) >= 4:
            und, expiry, right_ch, strike = parts[0], parts[1], parts[2], parts[3]
            try:
                return {
                    "underlying": und,
                    "right": OptionRight.CALL if right_ch.startswith("C") else OptionRight.PUT,
                    "strike": float(strike),
                    "expiry": expiry,
                    "instrument_type": InstrumentType.CALL_OPTION
                    if right_ch.startswith("C")
                    else InstrumentType.PUT_OPTION,
                }
            except ValueError:
                return None
    m = _OCC.match(s.replace(" ", ""))
    if m:
        root, yy, mm, dd, cp, strike8 = m.groups()
        itype = InstrumentType.CALL_OPTION if cp == "C" else InstrumentType.PUT_OPTION
        return {
            "underlying": root,
            "right": OptionRight.CALL if cp == "C" else OptionRight.PUT,
            "strike": int(strike8) / 1000.0,
            "expiry": f"20{yy}-{mm}-{dd}",
            "instrument_type": itype,
        }
    return None


def _spread_pct(c: dict) -> float | None:
    bid, ask, mid = c.get("bid"), c.get("ask"), c.get("mid")
    if not mid or bid is None or ask is None:
        return None
    return (ask - bid) / mid * 100 if mid else None


def select_call_contract(
    chain: list[dict],
    *,
    max_cost: float,
    delta_min: float,
    delta_max: float,
    max_spread_pct: float,
) -> dict | None:
    """Pick an ATM-ish, affordable, liquid CALL from an enriched chain slice (Model B)."""
    target = (delta_min + delta_max) / 2
    cands = []
    for c in chain:
        if c.get("right") != OptionRight.CALL.value:
            continue
        delta = c.get("delta")
        cost = c.get("cost")
        if delta is None or cost is None:
            continue
        if not (delta_min <= abs(delta) <= delta_max):
            continue
        if cost > max_cost:
            continue
        sp = _spread_pct(c)
        if sp is not None and sp > max_spread_pct:
            continue
        cands.append(c)
    if not cands:
        return None
    # Closest to the target delta wins (most ATM within the band).
    cands.sort(key=lambda c: abs(abs(c["delta"]) - target))
    return cands[0]

"""Kill-switch CLI (spec §36) — operator emergency stop, independent of Claude.

    apm-killswitch status
    apm-killswitch on  --note "market anomaly"
    apm-killswitch off

When engaged, the Safety Guard blocks ALL new orders (even SANDBOX); analysis, research,
and journaling continue. This is the DB-backed half of the kill switch; the env-level
``APM_TRADING_ENABLED`` is the other half.
"""

from __future__ import annotations

import argparse
import asyncio

from apm.observability import configure_logging
from apm.safety.killswitch import KillSwitch


async def _run(args: argparse.Namespace) -> None:
    ks = KillSwitch()
    if args.command == "status":
        stopped = await ks.is_emergency_stopped()
        print(f"emergency_stop: {'ENGAGED (no new orders)' if stopped else 'clear'}")
    elif args.command == "on":
        await ks.set_emergency_stop(True, note=args.note)
        print("emergency_stop ENGAGED — new orders blocked.")
    elif args.command == "off":
        await ks.set_emergency_stop(False, note=args.note)
        print("emergency_stop cleared.")


def main() -> None:
    configure_logging(level="WARNING", json=False)
    parser = argparse.ArgumentParser(prog="apm-killswitch", description="Emergency stop control")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show current emergency-stop state")
    on = sub.add_parser("on", help="engage emergency stop (block all new orders)")
    on.add_argument("--note", default=None)
    off = sub.add_parser("off", help="clear emergency stop")
    off.add_argument("--note", default=None)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()

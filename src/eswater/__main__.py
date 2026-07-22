"""Command-line interface for eswater.

Invaluable for debugging against the live API without Home Assistant::

    eswater meters --username you@example.com --password '...'
    eswater usage --username you@example.com --password '...' \\
        --account-id 3799250100 --meter-serial 24LU083349 \\
        --start 2026-07-18 --granularity hourly

Credentials can also be supplied via the ESW_USERNAME / ESW_PASSWORD
environment variables, or a ``.env`` file in the working directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import aiohttp

from .client import ESWaterClient
from .models import Granularity


def _load_dotenv(path: str = ".env") -> None:
    """Populate os.environ from a simple KEY=VALUE ``.env`` file, if present.

    Existing environment variables win (``setdefault``). No external dependency.
    """
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eswater", description=__doc__)
    parser.add_argument("--username", default=os.environ.get("ESW_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("ESW_PASSWORD"))

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("accounts", help="List accounts and their meters")
    sub.add_parser("meters", help="List all smart meters")

    usage = sub.add_parser("usage", help="Fetch usage readings for a meter")
    usage.add_argument("--account-id", required=True)
    usage.add_argument("--meter-serial", required=True)
    usage.add_argument(
        "--start",
        type=_parse_date,
        required=True,
        help="Target day (hourly) or window start (daily+), ISO e.g. 2026-07-18",
    )
    usage.add_argument(
        "--granularity",
        type=Granularity,
        choices=list(Granularity),
        default=Granularity.DAILY,
    )
    return parser


async def _run(args: argparse.Namespace) -> None:
    if not args.username or not args.password:
        raise SystemExit("Provide --username/--password or ESW_USERNAME/ESW_PASSWORD.")

    async with aiohttp.ClientSession() as session:
        client = ESWaterClient(session, args.username, args.password)
        await client.authenticate()

        if args.command == "accounts":
            result = [asdict(a) for a in await client.get_accounts()]
        elif args.command == "meters":
            result = [asdict(m) for m in await client.get_meters()]
        elif args.command == "usage":
            readings = await client.get_usage(
                args.account_id, args.meter_serial, args.start, args.granularity
            )
            result = [asdict(r) for r in readings]
        else:  # pragma: no cover - argparse enforces this
            raise SystemExit(f"Unknown command: {args.command}")

        print(json.dumps(result, indent=2, default=str))


def main() -> None:
    _load_dotenv()
    args = _build_parser().parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover
    main()

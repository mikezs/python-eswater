# eswater

Async Python client for **Essex & Suffolk Water** (Northumbrian Water Group)
smart water meter data.

Built to be consumed by a Home Assistant custom integration (see the companion
`ha-essex-suffolk-water` repo), but usable standalone.

> ⚠️ **Alpha.** The ESW/NWG API is private and undocumented; this library was
> built by reverse-engineering the web portal — see [`docs/api.md`](docs/api.md).
> Auth and usage retrieval are implemented and verified end-to-end against the
> live API, but the API is unofficial and may change or break without notice.

## Install

```bash
pip install eswater           # once published
# or, for development:
pip install -e ".[dev]"
```

## Usage

Log in with the **email + password** of your ESW online account.

```python
import asyncio
from datetime import datetime

import aiohttp
from eswater import ESWaterClient, Granularity


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        client = ESWaterClient(session, "you@example.com", "your-password")
        await client.authenticate()

        meter = (await client.get_meters())[0]
        # Hourly for a specific day (returns ~24 readings):
        usage = await client.get_usage(
            meter.account_id, meter.serial, datetime(2026, 7, 18), Granularity.HOURLY
        )
        for reading in usage:
            print(reading.timestamp, reading.consumption_litres, "L", reading.cost, "£")


asyncio.run(main())
```

Consumption is in **litres** and cost in **GBP**, both provided by the API.
The smart-usage JWT (~10 min) is refreshed automatically; the login session
(~1 hour) is re-established on expiry.

> **Timestamps are naive local (UK) time**, exactly as the API returns them —
> `UsageReading.timestamp` carries no `tzinfo`. Convert to your own zone before
> use (e.g. attach `ZoneInfo("Europe/London")`); don't assume UTC, or readings
> will be off by up to an hour across BST/GMT.

### CLI

Handy for debugging against the live API without Home Assistant:

```bash
export ESW_USERNAME=you@example.com ESW_PASSWORD='…'
eswater meters
eswater usage --account-id 3799250100 --meter-serial 24LU083349 \
    --start 2026-07-18 --granularity hourly
```

## Public API

| Method | Description |
| --- | --- |
| `authenticate()` | Log in (cookie session) and fetch the first smart JWT |
| `refresh()` | Re-fetch the short-lived smart-usage JWT (usually automatic) |
| `get_accounts()` | List billing accounts, each populated with meters |
| `get_meters()` | List all smart meters (each has `account_id` + `serial`) |
| `get_usage(account_id, serial, start_date, granularity)` | Readings (litres + £) |
| `get_meter_usage(meter, start_date, granularity)` | Same, taking a `Meter` |
| `get_latest_reading(account_id, serial)` | Most recent daily reading |

Errors are typed: `InvalidAuth`, `NotAuthenticated`, `ServiceUnavailable`,
`ApiError` (all subclass `ESWaterError`).

## Development

```bash
pip install -e ".[dev]"
ruff check .
mypy src
pytest
```

## Disclaimer

Not affiliated with or endorsed by Essex & Suffolk Water or Northumbrian Water
Group. Uses a private API that may change or restrict access at any time. For
personal use with your own account.

## License

MIT

"""Data models returned by :class:`eswater.client.ESWaterClient`.

Shapes follow the reverse-engineered API in ``docs/api.md`` (ESW/NWG portal).
Consumers depend only on these typed objects, not the raw API JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Granularity(StrEnum):
    """Aggregation levels for usage queries (maps to the Get*WaterUsage calls)."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 string (naive, as the API returns) into a datetime."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class Meter:
    """A single smart water meter (from GetAccountDetails ``Meters[]``)."""

    serial: str  # BadgeNumber
    account_id: str
    premise_id: str | None = None
    smart_point: str | None = None
    installed_date: datetime | None = None
    last_read: float | None = None
    last_read_date: datetime | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any], *, account_id: str, premise_id: str | None) -> Meter:
        return cls(
            serial=str(data["BadgeNumber"]),
            account_id=account_id,
            premise_id=premise_id,
            smart_point=data.get("smartPoint") or None,
            installed_date=_parse_dt(data.get("meterInstalledDate")),
            last_read=_parse_float(data.get("LastRead")),
            last_read_date=_parse_dt(data.get("LastReadDate")),
        )


@dataclass(slots=True)
class Account:
    """A billing account + premise, owning one or more meters."""

    account_id: str
    premise_id: str | None = None
    address: str | None = None
    is_smart: bool = False
    start_date: datetime | None = None
    num_occupiers: int | None = None
    meters: list[Meter] = field(default_factory=list)

    @classmethod
    def from_summary(cls, data: dict[str, Any]) -> Account:
        """Build the shell from a GetAccountSummary ``Accounts[]`` entry."""
        return cls(
            account_id=str(data["AccountID"]),
            premise_id=str(data["PremiseID"]) if data.get("PremiseID") else None,
            address=data.get("PropertyAddress"),
        )

    def apply_details(self, detail: dict[str, Any]) -> None:
        """Enrich with GetAccountDetails (smart flag, start date, meters)."""
        account = detail.get("Account", {})
        self.is_smart = bool(account.get("SmartMeter", False))
        self.start_date = _parse_dt(account.get("StartDate"))
        self.num_occupiers = account.get("NumberOfOccupiers")
        self.meters = [
            Meter.from_api(m, account_id=self.account_id, premise_id=self.premise_id)
            for m in detail.get("Meters", [])
        ]


@dataclass(slots=True)
class UsageReading:
    """Water consumption over a single interval (from Get*WaterUsage ``[]``).

    ``timestamp`` is the ``Date`` field as returned by the API (naive/local).
    For hourly data this is the *end* of the hour; for daily+ it is the day/period.
    Consumption is in **litres**, cost in **GBP**, both already provided upstream.
    """

    timestamp: datetime
    consumption_litres: float
    cost: float | None = None
    reading_type: int | None = None

    @property
    def is_estimated(self) -> bool:
        """Best-effort: ``ReadingType == 0`` appears to mean provisional/estimated.

        See docs/api.md TODO — not yet confirmed.
        """
        return self.reading_type == 0

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> UsageReading:
        ts = _parse_dt(data.get("Date"))
        if ts is None:
            raise ValueError(f"Usage reading missing/invalid Date: {data!r}")
        return cls(
            timestamp=ts,
            consumption_litres=_parse_float(data.get("LitreValue")) or 0.0,
            cost=_parse_float(data.get("MonetaryValue")),
            reading_type=data.get("ReadingType"),
        )

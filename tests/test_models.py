"""Tests for parsing captured API responses into models."""

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import load_fixture

from eswater.models import Account, Granularity, UsageReading, _parse_dt, _parse_float


def test_account_summary_and_details_parse() -> None:
    summary = load_fixture("account_summary.json")
    account = Account.from_summary(summary["Accounts"][0])
    assert account.account_id == "3799250100"
    assert account.premise_id == "1126750100"

    details = load_fixture("account_details.json")
    account.apply_details(details["AccountDetail"])
    assert account.is_smart is True
    assert account.start_date == datetime(2017, 1, 30)
    assert account.num_occupiers == 4
    assert len(account.meters) == 1

    meter = account.meters[0]
    assert meter.serial == "24LU083349"
    assert meter.account_id == "3799250100"
    assert meter.premise_id == "1126750100"
    assert meter.smart_point == "01185597"
    assert meter.last_read == 231.0
    assert meter.installed_date == datetime(2024, 3, 20)


def test_usage_reading_parses_litres_and_cost() -> None:
    rows = load_fixture("hourly_usage.json")
    readings = [UsageReading.from_api(r) for r in rows]

    assert readings[0].timestamp == datetime(2026, 7, 18, 1, 0, 0)
    assert readings[0].consumption_litres == 4.0
    assert readings[0].cost == 0.02
    assert readings[0].reading_type == 1
    assert readings[0].is_estimated is False

    # ReadingType 0 -> treated as provisional/estimated (best-effort)
    assert readings[2].reading_type == 0
    assert readings[2].is_estimated is True


def test_usage_reading_missing_date_raises() -> None:
    with pytest.raises(ValueError, match="Date"):
        UsageReading.from_api({"LitreValue": 5})


@pytest.mark.parametrize("value", [None, "", 123, "not-a-date", "2026-13-01T00:00:00"])
def test_parse_dt_returns_none_on_bad_input(value: object) -> None:
    assert _parse_dt(value) is None


def test_parse_dt_valid() -> None:
    assert _parse_dt("2026-07-18T01:00:00") == datetime(2026, 7, 18, 1, 0, 0)


@pytest.mark.parametrize("value", [None, "abc", object()])
def test_parse_float_returns_none_on_bad_input(value: object) -> None:
    assert _parse_float(value) is None


def test_parse_float_accepts_numeric_string() -> None:
    assert _parse_float("231.000000") == 231.0


def test_granularity_accepts_string() -> None:
    assert Granularity("hourly") is Granularity.HOURLY


def test_apply_details_handles_no_meters() -> None:
    account = Account("acc")
    account.apply_details({"Account": {}, "Meters": []})
    assert account.meters == []
    assert account.is_smart is False

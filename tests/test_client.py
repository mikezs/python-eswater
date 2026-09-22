"""Tests for ESWaterClient.

Error-mapping tests patch ``session.request`` with a tiny async-context-manager
fake (robust across aiohttp versions). Endpoint tests patch ``client._request``
to return captured fixture payloads and to assert the request bodies sent.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any

import aiohttp
import pytest
from conftest import load_fixture, make_jwt

from eswater.client import (
    ESWaterClient,
    _expired,
    _jwt_expiry,
    _parse_profile_expiry,
)
from eswater.exceptions import ApiError, InvalidAuth, NotAuthenticated, ServiceUnavailable
from eswater.models import Granularity

# --- request/response fake -------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int, *, json_data: Any = None, text_data: str = "") -> None:
        self.status = status
        self._json = json_data
        self._text = text_data

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def json(self, *, content_type: str | None = None) -> Any:
        return self._json

    async def text(self) -> str:
        return self._text


def _patch_response(client: ESWaterClient, response: _FakeResponse) -> None:
    def _request(*_args: Any, **_kwargs: Any) -> _FakeResponse:
        return response

    client._session.request = _request  # type: ignore[method-assign]  # noqa: SLF001


# --- error mapping ---------------------------------------------------------


async def test_request_maps_401_to_invalid_auth(client: ESWaterClient) -> None:
    _patch_response(client, _FakeResponse(401))
    with pytest.raises(InvalidAuth):
        await client._request("GET", "/x")  # noqa: SLF001


async def test_request_maps_500_to_service_unavailable(client: ESWaterClient) -> None:
    _patch_response(client, _FakeResponse(503))
    with pytest.raises(ServiceUnavailable):
        await client._request("GET", "/x")  # noqa: SLF001


async def test_request_maps_4xx_to_api_error(client: ESWaterClient) -> None:
    _patch_response(client, _FakeResponse(400, text_data="bad"))
    with pytest.raises(ApiError):
        await client._request("GET", "/x")  # noqa: SLF001


async def test_request_returns_json(client: ESWaterClient) -> None:
    _patch_response(client, _FakeResponse(200, json_data={"ok": True}))
    assert await client._request("GET", "/x") == {"ok": True}  # noqa: SLF001


# --- auth guards -----------------------------------------------------------


async def test_get_accounts_requires_auth(session: aiohttp.ClientSession) -> None:
    c = ESWaterClient(session, "u", "p", base_url="https://api.test")
    with pytest.raises(NotAuthenticated):
        await c.get_accounts()


async def test_get_usage_requires_auth(session: aiohttp.ClientSession) -> None:
    c = ESWaterClient(session, "u", "p", base_url="https://api.test")
    with pytest.raises(NotAuthenticated):
        await c.get_usage("acc", "serial", datetime.now(UTC))


# --- authenticate flow -----------------------------------------------------


async def test_authenticate_full_sequence(session: aiohttp.ClientSession) -> None:
    """Login body -> SaveUserProfile -> GetAccountSummary -> GetSmartAuthToken."""
    jwt = make_jwt(exp=datetime.now(UTC) + timedelta(minutes=10))
    login_body = {
        "RestException": None,
        "OtherException": None,
        "Response": {
            "access_token": "seed123",
            "refresh_token": "unused",
            "expires_in": "2099-01-01T00:00:00Z",
        },
    }
    c = ESWaterClient(session, "u", "p", base_url="https://api.test")
    calls: list[str] = []

    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        calls.append(path)
        if "Auth/Login" in path:
            return login_body
        if "GetSmartAuthToken" in path:
            # Seeded with the login access_token, sent as access_Token.
            assert kwargs["json_body"] == {"access_Token": "seed123"}
            return {"Id_token": jwt, "Refresh_token": "rotated456"}
        return None  # SaveUserProfile / GetAccountSummary

    c._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    await c.authenticate()

    assert c._authenticated is True  # noqa: SLF001
    assert c._jwt == jwt  # noqa: SLF001
    # access token stored as the smart-token seed for subsequent refreshes
    assert c._access_token == "seed123"  # noqa: SLF001
    # required order: SaveUserProfile + GetAccountSummary precede the smart token
    login_i = next(i for i, p in enumerate(calls) if "Auth/Login" in p)
    save_i = next(i for i, p in enumerate(calls) if "SaveUserProfile" in p)
    summary_i = next(i for i, p in enumerate(calls) if "GetAccountSummary" in p)
    token_i = next(i for i, p in enumerate(calls) if "GetSmartAuthToken" in p)
    assert login_i < save_i < token_i
    assert summary_i < token_i


async def test_authenticate_login_failure_raises(session: aiohttp.ClientSession) -> None:
    c = ESWaterClient(session, "u", "p", base_url="https://api.test")

    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        # Login returns an error envelope (no Response).
        return {"RestException": "bad creds", "Response": None}

    c._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(InvalidAuth):
        await c.authenticate()


async def test_authenticate_missing_access_token_raises(
    session: aiohttp.ClientSession,
) -> None:
    c = ESWaterClient(session, "u", "p", base_url="https://api.test")

    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        return {"Response": {"expires_in": "2099-01-01T00:00:00Z"}}  # no access_token

    c._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(InvalidAuth):
        await c.authenticate()


async def test_refresh_reseeds_from_access_token(client: ESWaterClient) -> None:
    """Each refresh re-seeds from the stored login access token (no rotation)."""
    client._access_token = "seed"  # noqa: SLF001
    jwt = make_jwt(exp=datetime.now(UTC) + timedelta(minutes=10))

    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        assert kwargs["json_body"] == {"access_Token": "seed"}
        return {"Id_token": jwt, "Refresh_token": "ignored"}

    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    await client.refresh()
    # The returned Refresh_token is not adopted; the access-token seed persists.
    assert client._access_token == "seed"  # noqa: SLF001
    assert client._jwt == jwt  # noqa: SLF001


# --- data endpoints --------------------------------------------------------


async def test_context_manager_authenticates(session: aiohttp.ClientSession) -> None:
    c = ESWaterClient(session, "u", "p", base_url="https://api.test")
    entered: list[bool] = []

    async def fake_auth() -> None:
        entered.append(True)

    c.authenticate = fake_auth  # type: ignore[method-assign]
    async with c as ctx:
        assert ctx is c
    assert entered == [True]


async def test_get_meters_flattens(client: ESWaterClient) -> None:
    summary = load_fixture("account_summary.json")
    details = load_fixture("account_details.json")

    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        return summary if "GetAccountSummary" in path else details

    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    meters = await client.get_meters()
    assert [m.serial for m in meters] == ["TESTMETER01"]


async def test_get_accounts_parses(client: ESWaterClient) -> None:
    summary = load_fixture("account_summary.json")
    details = load_fixture("account_details.json")

    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        if "GetAccountSummary" in path:
            return summary
        if "AddOrUpdateCustomerSession" in path:
            return True
        if "GetAccountDetails" in path:
            assert kwargs["json_body"]["AccountId"] == "1000000000"
            return details
        raise AssertionError(path)

    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    accounts = await client.get_accounts()
    assert len(accounts) == 1
    assert accounts[0].meters[0].serial == "TESTMETER01"


async def test_get_accounts_iterates_multiple(client: ESWaterClient) -> None:
    details = load_fixture("account_details.json")
    summary = {
        "Accounts": [
            {"AccountID": "1", "PremiseID": "10"},
            {"AccountID": "2", "PremiseID": "20"},
        ]
    }

    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        return summary if "GetAccountSummary" in path else details

    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    accounts = await client.get_accounts()
    assert [a.account_id for a in accounts] == ["1", "2"]


async def test_get_accounts_selects_session_before_each_details_call(
    client: ESWaterClient,
) -> None:
    """Each account must be (re)selected via AddOrUpdateCustomerSession before
    GetAccountDetails is called for it, or the portal scopes the response to
    whichever account is still bound and returns null Account/Meters fields.
    """
    summary = {
        "Accounts": [
            {"AccountID": "1", "PremiseID": "10"},
            {"AccountID": "2", "PremiseID": "20"},
        ]
    }
    real_details = {
        "1": {
            "AccountDetail": {
                "Account": {"SmartMeter": True, "NumberOfOccupiers": 2},
                "Meters": [{"BadgeNumber": "SERIAL-1"}],
            }
        },
        "2": {
            "AccountDetail": {
                "Account": {"SmartMeter": False, "NumberOfOccupiers": 3},
                "Meters": [{"BadgeNumber": "SERIAL-2"}],
            }
        },
    }
    null_details = {"AccountDetail": {"Account": {}, "Meters": None}}
    selected = ["1"]  # GetAccountSummary bound account "1" at login

    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        if "GetAccountSummary" in path:
            return summary
        if "AddOrUpdateCustomerSession" in path:
            body = kwargs["json_body"]
            assert body[0] == "PersonId:PersonId"
            selected[0] = body[1].split(":", 1)[1]
            return True
        if "GetAccountDetails" in path:
            account_id = kwargs["json_body"]["AccountId"]
            return real_details[account_id] if account_id == selected[0] else null_details
        raise AssertionError(path)

    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    accounts = await client.get_accounts()

    assert accounts[0].meters[0].serial == "SERIAL-1"
    assert accounts[1].is_smart is False
    assert accounts[1].num_occupiers == 3
    assert accounts[1].meters[0].serial == "SERIAL-2"


async def test_get_usage_builds_body_and_parses(client: ESWaterClient) -> None:
    rows = load_fixture("hourly_usage.json")
    captured: dict[str, Any] = {}

    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        captured["path"] = path
        captured["body"] = kwargs["json_body"]
        return rows

    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    readings = await client.get_usage(
        "1000000000", "TESTMETER01", datetime(2026, 7, 18), Granularity.HOURLY
    )

    assert "GetHourlyWaterUsage" in captured["path"]
    assert captured["body"] == {
        "AccountId": "1000000000",
        "Authorization": client._jwt,  # noqa: SLF001
        "MeterSerial": "TESTMETER01",
        "StartDate": "2026-07-18T00:00:00",
    }
    assert len(readings) == 3
    assert readings[0].consumption_litres == 4.0


async def test_get_usage_refreshes_expired_jwt(client: ESWaterClient) -> None:
    client._jwt = None  # noqa: SLF001 - force a refresh
    refreshed: list[bool] = []

    async def fake_refresh() -> None:
        refreshed.append(True)
        client._jwt = "new-jwt"  # noqa: SLF001

    async def fake_request(*_a: Any, **_k: Any) -> Any:
        return []

    client._do_refresh_token = fake_refresh  # type: ignore[method-assign]  # noqa: SLF001
    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    await client.get_usage("acc", "serial", datetime(2026, 7, 18))
    assert refreshed == [True]


async def test_concurrent_get_usage_refreshes_token_once(client: ESWaterClient) -> None:
    """Overlapping calls must not both spend the single-use refresh token."""
    client._jwt = None  # noqa: SLF001 - force a refresh
    client._jwt_expiry = None  # noqa: SLF001
    refreshes = 0

    async def fake_refresh() -> None:
        nonlocal refreshes
        refreshes += 1
        await asyncio.sleep(0)  # yield so the second caller can race the lock
        client._jwt = make_jwt(exp=datetime.now(UTC) + timedelta(minutes=10))  # noqa: SLF001
        client._jwt_expiry = datetime.now(UTC) + timedelta(minutes=10)  # noqa: SLF001

    async def fake_request(*_a: Any, **_k: Any) -> Any:
        return []

    client._do_refresh_token = fake_refresh  # type: ignore[method-assign]  # noqa: SLF001
    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    await asyncio.gather(
        client.get_usage("acc", "serial", datetime(2026, 7, 18)),
        client.get_usage("acc", "serial", datetime(2026, 7, 18)),
    )
    assert refreshes == 1


# --- jwt helper ------------------------------------------------------------


def test_jwt_expiry_decodes_exp() -> None:
    exp = datetime(2030, 1, 1, tzinfo=UTC)
    assert _jwt_expiry(make_jwt(exp=exp)) == exp


def test_jwt_expiry_handles_garbage() -> None:
    assert _jwt_expiry("not-a-jwt") is None


# --- more auth/refresh paths ----------------------------------------------


async def test_refresh_token_requires_auth(session: aiohttp.ClientSession) -> None:
    c = ESWaterClient(session, "u", "p", base_url="https://api.test")
    with pytest.raises(NotAuthenticated):
        await c.refresh()


async def test_refresh_token_missing_id_token_raises(client: ESWaterClient) -> None:
    async def fake_request(*_a: Any, **_k: Any) -> Any:
        return {"Access_token": "x"}  # no Id_token

    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(ApiError):
        await client.refresh()


async def test_ensure_token_reauthenticates_when_profile_expired(
    client: ESWaterClient,
) -> None:
    client._profile_expiry = datetime.now(UTC) - timedelta(seconds=1)  # noqa: SLF001
    reauth: list[bool] = []

    async def fake_auth() -> None:
        reauth.append(True)

    async def fake_request(*_a: Any, **_k: Any) -> Any:
        return []

    client._do_authenticate = fake_auth  # type: ignore[method-assign]  # noqa: SLF001
    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    await client.get_usage("acc", "serial", datetime(2026, 7, 18))
    assert reauth == [True]


async def test_ensure_token_falls_back_to_full_reauth_when_refresh_fails(
    client: ESWaterClient,
) -> None:
    """A failing token refresh (e.g. a desynced/stale rotating refresh
    token) falls back to a full re-login instead of surfacing the failure.
    """
    client._jwt = None  # noqa: SLF001 - force a refresh attempt
    reauthed: list[bool] = []

    async def fake_refresh() -> None:
        raise ApiError("GetSmartAuthToken returned no Id_token.")

    async def fake_authenticate() -> None:
        reauthed.append(True)
        client._jwt = "new-jwt"  # noqa: SLF001
        client._jwt_expiry = datetime.now(UTC) + timedelta(minutes=10)  # noqa: SLF001

    async def fake_request(*_a: Any, **_k: Any) -> Any:
        return []

    client._do_refresh_token = fake_refresh  # type: ignore[method-assign]  # noqa: SLF001
    client._do_authenticate = fake_authenticate  # type: ignore[method-assign]  # noqa: SLF001
    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    await client.get_usage("acc", "serial", datetime(2026, 7, 18))
    assert reauthed == [True]
    assert client._jwt == "new-jwt"  # noqa: SLF001


async def test_ensure_token_does_not_fallback_on_service_unavailable(
    client: ESWaterClient,
) -> None:
    """A transient/network failure propagates instead of triggering an
    immediate duplicate full-login attempt.
    """
    client._jwt = None  # noqa: SLF001 - force a refresh attempt

    async def fake_refresh() -> None:
        raise ServiceUnavailable("Request timed out.")

    async def fake_authenticate() -> None:
        raise AssertionError("should not fall back to full re-auth on transient errors")

    client._do_refresh_token = fake_refresh  # type: ignore[method-assign]  # noqa: SLF001
    client._do_authenticate = fake_authenticate  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(ServiceUnavailable):
        await client.get_usage("acc", "serial", datetime(2026, 7, 18))


# --- convenience + latest --------------------------------------------------


async def test_get_meter_usage_delegates(client: ESWaterClient) -> None:
    from eswater.models import Meter

    captured: dict[str, Any] = {}

    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        captured["body"] = kwargs["json_body"]
        return load_fixture("hourly_usage.json")

    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    meter = Meter(serial="TESTMETER01", account_id="1000000000")
    await client.get_meter_usage(meter, datetime(2026, 7, 18))
    assert captured["body"]["MeterSerial"] == "TESTMETER01"
    assert captured["body"]["AccountId"] == "1000000000"


async def test_get_latest_reading_returns_most_recent(client: ESWaterClient) -> None:
    async def fake_request(*_a: Any, **_k: Any) -> Any:
        return load_fixture("hourly_usage.json")

    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    latest = await client.get_latest_reading("acc", "serial")
    assert latest is not None
    assert latest.timestamp == datetime(2026, 7, 18, 6, 0, 0)


async def test_get_latest_reading_empty_returns_none(client: ESWaterClient) -> None:
    async def fake_request(*_a: Any, **_k: Any) -> Any:
        return []

    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    assert await client.get_latest_reading("acc", "serial") is None


async def test_get_usage_rejects_non_list_payload(client: ESWaterClient) -> None:
    async def fake_request(*_a: Any, **_k: Any) -> Any:
        return {"unexpected": "shape"}

    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(ApiError):
        await client.get_usage("acc", "serial", datetime(2026, 7, 18))


async def test_get_usage_wraps_parse_errors(client: ESWaterClient) -> None:
    """A renamed/missing field surfaces as ApiError, not a raw ValueError."""

    async def fake_request(*_a: Any, **_k: Any) -> Any:
        return [{"LitreValue": 5}]  # no Date -> from_api raises ValueError

    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(ApiError):
        await client.get_usage("acc", "serial", datetime(2026, 7, 18))


async def test_get_accounts_wraps_parse_errors(client: ESWaterClient) -> None:
    """A renamed required field surfaces as ApiError, not a raw KeyError."""

    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        return {"Accounts": [{"PremiseID": "10"}]}  # missing AccountID

    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(ApiError):
        await client.get_accounts()


# --- _request transport errors + text ------------------------------------


async def test_request_timeout_maps_to_service_unavailable(
    client: ESWaterClient,
) -> None:
    def _raise(*_a: Any, **_k: Any) -> Any:
        raise TimeoutError

    client._session.request = _raise  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(ServiceUnavailable):
        await client._request("GET", "/x")  # noqa: SLF001


async def test_request_client_error_maps_to_service_unavailable(
    client: ESWaterClient,
) -> None:
    def _raise(*_a: Any, **_k: Any) -> Any:
        raise aiohttp.ClientError("boom")

    client._session.request = _raise  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(ServiceUnavailable):
        await client._request("GET", "/x")  # noqa: SLF001


async def test_request_parse_json_false_returns_text(client: ESWaterClient) -> None:
    _patch_response(client, _FakeResponse(200, text_data="plain"))
    assert await client._request("POST", "/x", parse_json=False) == "plain"  # noqa: SLF001


# --- expiry helpers --------------------------------------------------------


def test_parse_profile_expiry_variants() -> None:
    assert _parse_profile_expiry(None) is None
    assert _parse_profile_expiry("nonsense") is None
    dt = _parse_profile_expiry("2099-01-01T00:00:00Z")
    assert dt is not None and dt.tzinfo is not None


def test_parse_profile_expiry_naive_gets_utc() -> None:
    dt = _parse_profile_expiry("2099-01-01T00:00:00")
    assert dt is not None and dt.tzinfo is UTC


def test_expired_true_and_false() -> None:
    assert _expired(datetime.now(UTC) - timedelta(seconds=1), margin=0) is True
    assert _expired(datetime.now(UTC) + timedelta(hours=1), margin=30) is False

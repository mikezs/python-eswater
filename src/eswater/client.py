"""Async client for the Essex & Suffolk Water (NWG) portal API.

Auth is session based (see docs/api.md):
  1. POST /api/Auth/Login {email, password} -> sets the .AspNetCore.Session cookie
     and returns ``Response.refresh_token`` / ``expires_in`` in the body
  2. POST /api/Customer/GetSmartAuthToken {refresh_Token} -> short-lived JWT
  3. usage calls send the JWT in the request *body* (``Authorization``) + cookies
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

import aiohttp

from .const import (
    ACCOUNT_DETAILS_PATH,
    ACCOUNT_SUMMARY_PATH,
    ADD_OR_UPDATE_SESSION_PATH,
    BASE_URL,
    DEFAULT_TIMEOUT,
    JWT_EXPIRY_MARGIN,
    LOGIN_PATH,
    SAVE_USER_PROFILE_PATH,
    SMART_AUTH_TOKEN_PATH,
    USAGE_PATHS,
    USER_AGENT,
    XHR_HEADER,
)
from .exceptions import (
    ApiError,
    InvalidAuth,
    NotAuthenticated,
    ServiceUnavailable,
)
from .models import Account, Granularity, Meter, UsageReading

_LOGGER = logging.getLogger(__name__)

_USAGE_DATE_FMT = "%Y-%m-%dT%H:%M:%S"


def _jwt_expiry(token: str) -> datetime | None:
    """Return the ``exp`` of a JWT as an aware UTC datetime (no verification)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
    except (IndexError, KeyError, ValueError, binascii.Error, json.JSONDecodeError):
        return None


class ESWaterClient:
    """Client for retrieving Essex & Suffolk Water smart meter data.

    Example
    -------
    >>> async with aiohttp.ClientSession() as session:
    ...     client = ESWaterClient(session, "you@example.com", "password")
    ...     await client.authenticate()
    ...     for meter in await client.get_meters():
    ...         usage = await client.get_usage(
    ...             meter.account_id, meter.serial, meter.installed_date
    ...         )
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        *,
        base_url: str = BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)

        self._authenticated = False
        self._refresh_token: str | None = None
        self._profile_expiry: datetime | None = None
        self._jwt: str | None = None
        self._jwt_expiry: datetime | None = None
        # Serialises token refresh / re-auth so overlapping calls don't both
        # spend the single-use rotating refresh token (see docs/api.md).
        self._auth_lock = asyncio.Lock()

    # -- context manager ------------------------------------------------------

    async def __aenter__(self) -> ESWaterClient:
        await self.authenticate()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    # -- authentication -------------------------------------------------------

    async def authenticate(self) -> None:
        """Log in, establishing the session cookie and fetching the first JWT.

        The login response carries the LoginRadius ``refresh_token`` in its body
        (``Response.refresh_token``); the ``.AspNetCore.Session`` cookie set
        alongside it authenticates subsequent calls via the session cookie jar.
        The profile must then be registered in the session via
        ``SaveUserProfile`` (else ``GetAccountSummary`` fails "PersonId is
        required"), and ``GetAccountSummary`` must be called before the smart
        token is fetched — it binds the account to the session, without which
        the usage endpoints return 401.

        Raises
        ------
        InvalidAuth
            If credentials are rejected or the token is absent from the response.
        ServiceUnavailable
            If the portal is unreachable.
        """
        async with self._auth_lock:
            await self._do_authenticate()

    async def _do_authenticate(self) -> None:
        """Login sequence. Caller must hold ``self._auth_lock``."""
        data = await self._request(
            "POST",
            LOGIN_PATH,
            json_body={"email": self._username, "password": self._password},
        )
        response = data.get("Response") if isinstance(data, dict) else None
        if not response or data.get("RestException") or data.get("OtherException"):
            raise InvalidAuth("Login failed (check credentials).")

        self._refresh_token = response.get("refresh_token")
        if not self._refresh_token:
            raise InvalidAuth("Login succeeded but no refresh_token in response.")

        # Register the profile, then establish account context in the session.
        # Both are prerequisites for a smart token that the usage endpoints accept.
        await self._request("POST", SAVE_USER_PROFILE_PATH, json_body=response)
        await self._request(
            "GET", ACCOUNT_SUMMARY_PATH, params={"personId": "PersonId"}
        )

        self._profile_expiry = _parse_profile_expiry(response.get("expires_in"))
        self._authenticated = True

        await self._do_refresh_token()

    async def refresh(self) -> None:
        """Exchange the current ``refresh_token`` for a fresh smart-usage JWT.

        The smart-auth refresh token is single-use and rotates on each call, so
        the returned ``Refresh_token`` is stored for the next refresh. Usually
        called automatically; exposed for manual control.
        """
        async with self._auth_lock:
            await self._do_refresh_token()

    async def _do_refresh_token(self) -> None:
        """Fetch a fresh smart JWT. Caller must hold ``self._auth_lock``."""
        if not self._refresh_token:
            raise NotAuthenticated("Not authenticated; call authenticate() first.")
        data = await self._request(
            "POST",
            SMART_AUTH_TOKEN_PATH,
            json_body={"refresh_Token": self._refresh_token},
        )
        token = data.get("Id_token") if isinstance(data, dict) else None
        if not token:
            raise ApiError("GetSmartAuthToken returned no Id_token.")
        self._jwt = token
        self._jwt_expiry = _jwt_expiry(token)
        # Rotate: the refresh token just used is now spent.
        if rotated := data.get("Refresh_token"):
            self._refresh_token = rotated

    async def _ensure_token(self) -> None:
        """Guarantee a usable JWT, re-logging-in / refreshing as needed.

        The check-and-refresh runs under ``self._auth_lock`` and re-evaluates
        expiry once the lock is held, so overlapping callers don't each fire a
        refresh and spend the single-use rotating refresh token more than once.
        """
        if not self._authenticated:
            raise NotAuthenticated("Call authenticate() before making requests.")
        async with self._auth_lock:
            if self._profile_expiry and _expired(self._profile_expiry, margin=0):
                await self._do_authenticate()
                return
            if self._jwt is None or (
                self._jwt_expiry and _expired(self._jwt_expiry, margin=JWT_EXPIRY_MARGIN)
            ):
                await self._do_refresh_token()

    # -- data endpoints -------------------------------------------------------

    async def get_accounts(self) -> list[Account]:
        """Return all billing accounts, each populated with its meters."""
        if not self._authenticated:
            raise NotAuthenticated("Call authenticate() before making requests.")
        summary = await self._request(
            "GET", ACCOUNT_SUMMARY_PATH, params={"personId": "PersonId"}
        )
        try:
            accounts = [Account.from_summary(a) for a in summary.get("Accounts") or []]
            for account in accounts:
                # The portal session is bound to a single account at a time
                # (GetAccountSummary bound it during authenticate()); re-bind
                # before each GetAccountDetails call or the response comes
                # back with null Account/Meters fields for any other account.
                await self._request(
                    "POST",
                    ADD_OR_UPDATE_SESSION_PATH,
                    json_body=["PersonId:PersonId", f"AccountId:{account.account_id}"],
                )
                detail = await self._request(
                    "POST",
                    ACCOUNT_DETAILS_PATH,
                    json_body={
                        "AccountId": account.account_id,
                        "PremiseId": account.premise_id,
                        "PersonId": "PersonId",
                    },
                )
                account.apply_details(detail.get("AccountDetail") or {})
        except (KeyError, ValueError, TypeError, AttributeError) as err:
            raise ApiError(f"Could not parse account response: {err}") from err
        return accounts

    async def get_meters(self) -> list[Meter]:
        """Return all smart meters across all accounts."""
        return [m for account in await self.get_accounts() for m in account.meters]

    async def get_usage(
        self,
        account_id: str,
        meter_serial: str,
        start_date: datetime,
        granularity: Granularity | str = Granularity.DAILY,
    ) -> list[UsageReading]:
        """Return consumption readings for a meter.

        Parameters
        ----------
        account_id, meter_serial:
            From :meth:`get_meters` (``meter.account_id`` / ``meter.serial``).
        start_date:
            For ``HOURLY`` this is the target day (returns ~24 readings). For
            ``DAILY`` and coarser the API returns a rolling recent window
            regardless (see docs/api.md).
        granularity:
            One of :class:`~eswater.models.Granularity`.
        """
        granularity = Granularity(granularity)
        await self._ensure_token()
        readings = await self._request(
            "POST",
            USAGE_PATHS[granularity.value],
            json_body={
                "AccountId": account_id,
                "Authorization": self._jwt,
                "MeterSerial": meter_serial,
                "StartDate": start_date.strftime(_USAGE_DATE_FMT),
            },
        )
        if not isinstance(readings, list):
            raise ApiError(f"Unexpected usage payload: {readings!r}")
        try:
            return [UsageReading.from_api(r) for r in readings]
        except (KeyError, ValueError, TypeError) as err:
            raise ApiError(f"Could not parse usage response: {err}") from err

    async def get_meter_usage(
        self,
        meter: Meter,
        start_date: datetime,
        granularity: Granularity | str = Granularity.DAILY,
    ) -> list[UsageReading]:
        """Convenience wrapper around :meth:`get_usage` taking a :class:`Meter`."""
        return await self.get_usage(meter.account_id, meter.serial, start_date, granularity)

    async def get_latest_reading(
        self, account_id: str, meter_serial: str
    ) -> UsageReading | None:
        """Return the most recent daily reading for a meter, if any."""
        readings = await self.get_usage(
            account_id, meter_serial, datetime.now(UTC), Granularity.DAILY
        )
        return max(readings, key=lambda r: r.timestamp, default=None)

    # -- internals ------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | list[Any] | None = None,
        parse_json: bool = True,
    ) -> Any:
        """Make a cookie-authenticated request and map errors to exceptions.

        The session's cookie jar carries auth, so no Authorization header is set.
        Some endpoints return ``text/plain`` JSON, so content-type is ignored
        when decoding.
        """
        url = f"{self._base_url}{path}"
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            **XHR_HEADER,
        }
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=self._timeout,
            ) as resp:
                if resp.status == 401:
                    raise InvalidAuth("Session rejected (401).")
                if resp.status >= 500:
                    raise ServiceUnavailable(f"Upstream error {resp.status}.")
                if resp.status >= 400:
                    body = await resp.text()
                    raise ApiError(f"Unexpected status {resp.status}: {body[:200]}")
                if not parse_json:
                    return await resp.text()
                return await resp.json(content_type=None)
        except TimeoutError as err:
            raise ServiceUnavailable("Request timed out.") from err
        except aiohttp.ClientError as err:
            raise ServiceUnavailable(str(err)) from err


def _parse_profile_expiry(value: Any) -> datetime | None:
    """Parse the ``userProfile.expires_in`` ISO timestamp into aware UTC."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _expired(when: datetime, *, margin: int) -> bool:
    """True if ``when`` is at/within ``margin`` seconds of now (UTC)."""
    remaining = (when - datetime.now(UTC)).total_seconds()
    return remaining <= margin

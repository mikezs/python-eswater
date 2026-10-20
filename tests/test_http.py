"""Exercise the real HTTP transport and cookie jar against a local portal."""

from datetime import UTC, datetime, timedelta

import aiohttp
import pytest
from aiohttp import web
from conftest import load_fixture, make_jwt

from eswater import ApiError, ESWaterClient, Granularity


@pytest.mark.parametrize("invalid_json", [False, True])
async def test_local_portal_cookie_auth_and_usage(invalid_json: bool) -> None:
    """Login cookies survive real requests, stay private, and bad JSON is typed."""
    seen: list[str] = []
    jwt = make_jwt(exp=datetime.now(UTC) + timedelta(minutes=10))

    async def handle(request: web.Request) -> web.Response:
        seen.append(request.path)
        if request.path.endswith("/Login"):
            body = await request.json()
            assert body == {"email": "test@example.com", "password": "test"}
            response = web.json_response(
                {"Response": {"access_token": "seed", "expires_in": "2099-01-01T00:00:00Z"}}
            )
            response.set_cookie("portal_session", "private")
            return response
        assert request.cookies.get("portal_session") == "private"
        if request.path.endswith("/GetSmartAuthToken"):
            return web.json_response({"Id_token": jwt})
        if request.path.endswith("/GetAccountSummary"):
            return web.json_response(load_fixture("account_summary.json"))
        if request.path.endswith("/GetAccountDetails"):
            return web.json_response(load_fixture("account_details.json"))
        if request.path.endswith("/GetHourlyWaterUsage"):
            body = await request.json()
            assert body["Authorization"] == jwt
            if invalid_json:
                return web.Response(text="<html>maintenance</html>")
            return web.json_response(load_fixture("hourly_usage.json"))
        return web.json_response(True)

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    url = f"http://localhost:{port}"
    try:
        async with aiohttp.ClientSession() as session, aiohttp.ClientSession() as other:
            client = ESWaterClient(session, "test@example.com", "test", base_url=url)
            await client.authenticate()
            meter = (await client.get_meters())[0]
            assert not other.cookie_jar.filter_cookies(aiohttp.client_reqrep.URL(url))
            if invalid_json:
                with pytest.raises(ApiError, match="invalid JSON"):
                    await client.get_meter_usage(meter, datetime(2026, 7, 18), Granularity.HOURLY)
            else:
                rows = await client.get_meter_usage(
                    meter, datetime(2026, 7, 18), Granularity.HOURLY
                )
                assert len(rows) == 3
                assert rows[0].consumption_litres == 4.0
            assert len(seen) == 8
        assert session.closed
    finally:
        await runner.cleanup()

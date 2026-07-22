"""Shared pytest fixtures and helpers."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
import pytest_asyncio

from eswater.client import ESWaterClient

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def make_jwt(*, exp: datetime) -> str:
    """Build an unsigned JWT with the given expiry (for expiry-parsing tests)."""

    def _b64(obj: dict[str, Any]) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{_b64({'alg': 'none'})}.{_b64({'exp': int(exp.timestamp())})}.sig"


@pytest_asyncio.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as sess:
        yield sess


@pytest_asyncio.fixture
async def client(session: aiohttp.ClientSession) -> ESWaterClient:
    """An authenticated client with a valid, unexpired smart JWT."""
    c = ESWaterClient(session, "you@example.com", "password", base_url="https://api.test")
    c._authenticated = True  # noqa: SLF001
    c._refresh_token = "seed-refresh-token"  # noqa: SLF001
    c._profile_expiry = datetime.now(UTC) + timedelta(hours=1)  # noqa: SLF001
    c._jwt = make_jwt(exp=datetime.now(UTC) + timedelta(minutes=10))  # noqa: SLF001
    c._jwt_expiry = datetime.now(UTC) + timedelta(minutes=10)  # noqa: SLF001
    return c

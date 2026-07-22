"""Tests for the eswater command-line interface."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any

import pytest

import eswater.__main__ as cli
from eswater.models import Granularity, Meter, UsageReading


def test_load_dotenv(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text('# comment\nESW_USERNAME=you@example.com\nESW_PASSWORD="pw with spaces"\n\n')
    monkeypatch.delenv("ESW_USERNAME", raising=False)
    monkeypatch.delenv("ESW_PASSWORD", raising=False)
    try:
        cli._load_dotenv(str(env))
        assert cli.os.environ["ESW_USERNAME"] == "you@example.com"
        assert cli.os.environ["ESW_PASSWORD"] == "pw with spaces"
    finally:
        # _load_dotenv writes to the real os.environ; clean up so it can't
        # leak credentials into other tests.
        cli.os.environ.pop("ESW_USERNAME", None)
        cli.os.environ.pop("ESW_PASSWORD", None)


def test_load_dotenv_missing_file_is_noop() -> None:
    cli._load_dotenv("/nonexistent/.env")  # must not raise


def test_load_dotenv_does_not_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("ESW_USERNAME", "existing")
    env = tmp_path / ".env"
    env.write_text("ESW_USERNAME=fromfile\n")
    cli._load_dotenv(str(env))
    assert cli.os.environ["ESW_USERNAME"] == "existing"


def test_parse_date() -> None:
    assert cli._parse_date("2026-07-18") == datetime(2026, 7, 18)


def test_build_parser_usage_defaults() -> None:
    args = cli._build_parser().parse_args(
        ["--username", "u", "--password", "p", "usage",
         "--account-id", "a", "--meter-serial", "s", "--start", "2026-07-18"]
    )
    assert args.command == "usage"
    assert args.granularity is Granularity.DAILY
    assert args.start == datetime(2026, 7, 18)


async def test_run_without_credentials_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ESW_USERNAME", raising=False)
    monkeypatch.delenv("ESW_PASSWORD", raising=False)
    args = cli._build_parser().parse_args(["meters"])
    with pytest.raises(SystemExit):
        await cli._run(args)


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None


class _FakeClient:
    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def authenticate(self) -> None:
        return None

    async def get_meters(self) -> list[Meter]:
        return [Meter(serial="TESTMETER01", account_id="1000000000")]

    async def get_accounts(self) -> list[Any]:
        return []

    async def get_usage(self, *_a: Any, **_k: Any) -> list[UsageReading]:
        return [UsageReading(timestamp=datetime(2026, 7, 18, 1), consumption_litres=4.0, cost=0.02)]


@pytest.fixture
def _patch_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.aiohttp, "ClientSession", lambda: _FakeSession())
    monkeypatch.setattr(cli, "ESWaterClient", _FakeClient)


async def test_run_meters(_patch_cli: None, capsys: pytest.CaptureFixture[str]) -> None:
    args = cli._build_parser().parse_args(["--username", "u", "--password", "p", "meters"])
    await cli._run(args)
    out = json.loads(capsys.readouterr().out)
    assert out[0]["serial"] == "TESTMETER01"


async def test_run_usage(_patch_cli: None, capsys: pytest.CaptureFixture[str]) -> None:
    args = cli._build_parser().parse_args(
        ["--username", "u", "--password", "p", "usage",
         "--account-id", "a", "--meter-serial", "s", "--start", "2026-07-18",
         "--granularity", "hourly"]
    )
    await cli._run(args)
    out = json.loads(capsys.readouterr().out)
    assert out[0]["consumption_litres"] == 4.0


async def test_run_accounts(_patch_cli: None, capsys: pytest.CaptureFixture[str]) -> None:
    args = cli._build_parser().parse_args(["--username", "u", "--password", "p", "accounts"])
    await cli._run(args)
    assert json.loads(capsys.readouterr().out) == []


def test_main_invokes_run(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Any] = []

    async def fake_run(args: Any) -> None:
        called.append(args.command)

    monkeypatch.setattr(cli, "_load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(sys, "argv", ["eswater", "--username", "u", "--password", "p", "meters"])
    cli.main()
    assert called == ["meters"]

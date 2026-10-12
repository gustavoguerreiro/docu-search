import asyncio
import logging
from typing import Any

import pytest
from pydantic import SecretStr
from pymongo.errors import ServerSelectionTimeoutError

from docusearch.core.settings import Settings
from docusearch.db.mongo import MongoClient, create_client, get_database, ping

# Fake credential used only to prove that it never reaches logs.
SECRET_URI = "mongodb://appuser:s3cr3t-pass@db.internal:27017"


class FakeAdmin:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.commands: list[str] = []

    async def command(self, name: str) -> dict[str, float]:
        self.commands.append(name)
        if callable(self.outcome):
            await self.outcome()
        elif isinstance(self.outcome, Exception):
            raise self.outcome
        return {"ok": 1.0}


class FakeClient:
    def __init__(self, outcome: Any = None) -> None:
        self.admin = FakeAdmin(outcome)


def as_client(fake: FakeClient) -> MongoClient:
    return fake  # type: ignore[return-value]


async def test_create_client_is_utc_aware_and_bounded() -> None:
    settings = Settings(_env_file=None, mongodb_uri=SecretStr(SECRET_URI), mongodb_timeout_ms=1500)

    client = create_client(settings)
    try:
        assert client.options.codec_options.tz_aware is True
        assert client.options.server_selection_timeout == 1.5
        assert client.options.pool_options.appname == "docusearch"
    finally:
        await client.close()


async def test_create_client_does_not_connect_eagerly() -> None:
    # Port 1 is unroutable for MongoDB; creating and closing must still succeed instantly.
    settings = Settings(_env_file=None, mongodb_uri=SecretStr("mongodb://127.0.0.1:1"))

    client = create_client(settings)
    await asyncio.wait_for(client.close(), timeout=1)


async def test_get_database_uses_configured_name() -> None:
    settings = Settings(_env_file=None, mongodb_db_name="docusearch_test")
    client = create_client(settings)
    try:
        assert get_database(client, settings).name == "docusearch_test"
    finally:
        await client.close()


async def test_ping_true_when_server_answers() -> None:
    fake = FakeClient()

    assert await ping(as_client(fake), timeout_seconds=1) is True
    assert fake.admin.commands == ["ping"]


async def test_ping_false_on_driver_error_and_logs_only_the_error_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeClient(ServerSelectionTimeoutError(f"cannot reach {SECRET_URI}"))

    with caplog.at_level(logging.WARNING):
        assert await ping(as_client(fake), timeout_seconds=1) is False

    assert caplog.records[0].__dict__["error_type"] == "ServerSelectionTimeoutError"
    assert "s3cr3t-pass" not in caplog.text


async def test_ping_false_when_the_server_hangs() -> None:
    async def hang() -> None:
        await asyncio.sleep(10)

    assert await ping(as_client(FakeClient(hang)), timeout_seconds=0.05) is False


async def test_ping_against_unreachable_server_fails_fast() -> None:
    settings = Settings(
        _env_file=None,
        mongodb_uri=SecretStr("mongodb://127.0.0.1:1"),
        mongodb_timeout_ms=200,
    )
    client = create_client(settings)
    try:
        assert await ping(client, timeout_seconds=2) is False
    finally:
        await client.close()

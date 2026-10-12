from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr
from pymongo.errors import InvalidOperation

from docusearch.core.settings import Settings
from docusearch.db.mongo import MongoClient, MongoDatabase, create_client, ping
from docusearch.main import create_app


async def test_ping_succeeds_against_a_real_server(mongo_client: MongoClient) -> None:
    assert await ping(mongo_client, timeout_seconds=2) is True


async def test_datetimes_are_utc_aware_after_a_round_trip(mongo_db: MongoDatabase) -> None:
    written = datetime(2026, 10, 12, 15, 30, 0, 123000, tzinfo=UTC)

    inserted = await mongo_db["probe"].insert_one({"at": written})
    stored = await mongo_db["probe"].find_one({"_id": inserted.inserted_id})

    assert stored is not None
    assert stored["at"].tzinfo is not None
    assert stored["at"] == written


async def test_a_closed_client_refuses_further_use(mongo_settings: Settings) -> None:
    client = create_client(mongo_settings)
    assert await ping(client, timeout_seconds=2) is True

    await client.close()

    with pytest.raises(InvalidOperation):
        await client.admin.command("ping")
    assert await ping(client, timeout_seconds=2) is False


async def test_test_databases_exist_while_used(
    mongo_client: MongoClient, mongo_db: MongoDatabase
) -> None:
    await mongo_db["probe"].insert_one({"k": 1})

    assert mongo_db.name in await mongo_client.list_database_names()


async def test_app_reports_ready_and_releases_the_client(mongo_settings: Settings) -> None:
    app = create_app(mongo_settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.get("/api/v1/health/ready")
        client: MongoClient = app.state.mongo_client

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"mongodb": "ok", "indexes": "ok"}}
    with pytest.raises(InvalidOperation):
        await client.admin.command("ping")


async def test_app_reports_unavailable_when_mongodb_is_unreachable(
    mongo_settings: Settings,
) -> None:
    dead = mongo_settings.model_copy(
        update={"mongodb_uri": SecretStr("mongodb://127.0.0.1:1"), "mongodb_timeout_ms": 300}
    )
    app = create_app(dead)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            ready = await http.get("/api/v1/health/ready")
            live = await http.get("/api/v1/health/live")

    assert ready.status_code == 503
    assert ready.json() == {
        "status": "unavailable",
        "checks": {"mongodb": "fail", "indexes": "fail"},
    }
    assert live.status_code == 200


async def test_startup_creates_the_indexes_in_the_configured_database(
    mongo_settings: Settings, mongo_db: MongoDatabase
) -> None:
    app = create_app(mongo_settings)

    async with app.router.lifespan_context(app):
        users = await mongo_db["users"].index_information()

    assert "email_unique" in users

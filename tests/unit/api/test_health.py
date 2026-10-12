import asyncio
import logging
import time

import httpx
import pytest
from fastapi import FastAPI

from docusearch.core.settings import Settings
from docusearch.main import create_app


@pytest.fixture
def app() -> FastAPI:
    return create_app(Settings(_env_file=None))


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_live_returns_ok(app: FastAPI) -> None:
    async with client_for(app) as client:
        response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_live_ignores_failing_dependencies(app: FastAPI) -> None:
    async def down() -> bool:
        return False

    app.state.readiness_checks = {"database": down}

    async with client_for(app) as client:
        response = await client.get("/api/v1/health/live")

    assert response.status_code == 200


async def test_ready_is_ok_when_no_checks_are_registered(app: FastAPI) -> None:
    async with client_for(app) as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {}}


async def test_ready_is_ok_when_all_checks_pass(app: FastAPI) -> None:
    async def up() -> bool:
        return True

    app.state.readiness_checks = {"database": up, "queue": up}

    async with client_for(app) as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "queue": "ok"}}


async def test_ready_is_503_when_a_check_fails(app: FastAPI) -> None:
    async def up() -> bool:
        return True

    async def down() -> bool:
        return False

    app.state.readiness_checks = {"database": down, "queue": up}

    async with client_for(app) as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "fail", "queue": "ok"},
    }


async def test_ready_treats_a_raising_check_as_failed_without_leaking_details(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    async def explode() -> bool:
        raise ConnectionError("mongodb://user:pa55word@db:27017 refused")

    app.state.readiness_checks = {"database": explode}

    with caplog.at_level(logging.WARNING):
        async with client_for(app) as client:
            response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": {"database": "fail"}}
    assert "pa55word" not in response.text
    assert any(record.__dict__.get("check") == "database" for record in caplog.records)


async def test_health_endpoints_exist_only_under_api_v1(app: FastAPI) -> None:
    async with client_for(app) as client:
        response = await client.get("/health/live")

    assert response.status_code == 404


async def test_openapi_documents_health_endpoints(app: FastAPI) -> None:
    async with client_for(app) as client:
        schema = (await client.get("/openapi.json")).json()

    ready = schema["paths"]["/api/v1/health/ready"]["get"]
    assert "/api/v1/health/live" in schema["paths"]
    assert {"200", "503"} <= set(ready["responses"])
    assert ready["tags"] == ["health"]


async def test_ready_runs_checks_concurrently_and_keeps_all_results(app: FastAPI) -> None:
    async def slow_ok() -> bool:
        await asyncio.sleep(0.3)
        return True

    async def slow_fail() -> bool:
        await asyncio.sleep(0.3)
        return False

    app.state.readiness_checks = {"a": slow_ok, "b": slow_fail, "c": slow_ok}

    async with client_for(app) as client:
        started = time.perf_counter()
        response = await client.get("/api/v1/health/ready")
        elapsed = time.perf_counter() - started

    assert response.status_code == 503
    assert response.json()["checks"] == {"a": "ok", "b": "fail", "c": "ok"}
    assert elapsed < 0.8  # sequential execution would take ~0.9s

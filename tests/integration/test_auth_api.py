import statistics
import time
from collections.abc import AsyncIterator

import httpx
import pytest

from docusearch.core.settings import Settings
from docusearch.core.tokens import decode_access_token
from docusearch.db import collections
from docusearch.db.mongo import MongoDatabase
from docusearch.main import create_app

PASSWORD = "a-reasonably-long-password"


@pytest.fixture
async def api(
    mongo_settings: Settings, mongo_db: MongoDatabase
) -> AsyncIterator[httpx.AsyncClient]:
    """The real application, with its real lifespan, over a throwaway database."""
    app = create_app(mongo_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def register(api: httpx.AsyncClient, email: str, password: str = PASSWORD) -> httpx.Response:
    return await api.post("/api/v1/auth/register", json={"email": email, "password": password})


async def login(api: httpx.AsyncClient, email: str, password: str = PASSWORD) -> httpx.Response:
    return await api.post("/api/v1/auth/login", json={"email": email, "password": password})


# --- registration ------------------------------------------------------------------------------


async def test_register_creates_an_account_with_a_normalized_email_and_hashed_password(
    api: httpx.AsyncClient, mongo_db: MongoDatabase
) -> None:
    response = await register(api, "  Ana@Example.COM ")

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    stored = await mongo_db[collections.USERS].find_one({})
    assert stored is not None
    assert stored["email"] == "ana@example.com"
    assert stored["password_hash"].startswith("$argon2id$")
    assert PASSWORD not in str(stored)


async def test_registering_an_existing_email_looks_identical_and_changes_nothing(
    api: httpx.AsyncClient, mongo_db: MongoDatabase
) -> None:
    first = await register(api, "ana@example.com")
    second = await register(api, "ANA@example.com", "a-completely-different-password")

    assert (second.status_code, second.json()) == (first.status_code, first.json())
    assert await mongo_db[collections.USERS].count_documents({}) == 1
    assert (await login(api, "ana@example.com")).status_code == 200  # original password still works
    assert (
        await login(api, "ana@example.com", "a-completely-different-password")
    ).status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "ana@example.com", "password": "short"},
        {"email": "ana@example.com", "password": "x" * 129},
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "ana@example.com"},
        {"password": PASSWORD},
        {"email": "ana@example.com", "password": PASSWORD, "is_admin": True},
        {},
    ],
)
async def test_invalid_registrations_are_rejected_with_the_common_error_body(
    api: httpx.AsyncClient, mongo_db: MongoDatabase, payload: dict[str, object]
) -> None:
    response = await api.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert PASSWORD not in response.text
    assert await mongo_db[collections.USERS].count_documents({}) == 0


# --- login -------------------------------------------------------------------------------------


async def test_login_returns_working_tokens_and_forbids_caching(
    api: httpx.AsyncClient, mongo_db: MongoDatabase, mongo_settings: Settings
) -> None:
    await register(api, "ana@example.com")

    response = await login(api, "ANA@example.com")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "refresh_token"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == mongo_settings.access_token_ttl_minutes * 60
    assert response.headers["cache-control"] == "no-store"
    user = await mongo_db[collections.USERS].find_one({"email": "ana@example.com"})
    assert user is not None
    assert decode_access_token(body["access_token"], mongo_settings).user_id == user["_id"]
    stored = await mongo_db[collections.REFRESH_TOKENS].find_one({"user_id": user["_id"]})
    assert stored is not None
    assert stored["token_hash"] != body["refresh_token"]  # only the hash is kept


async def test_wrong_password_and_unknown_email_are_indistinguishable(
    api: httpx.AsyncClient,
) -> None:
    await register(api, "ana@example.com")

    wrong = await login(api, "ana@example.com", "not-the-right-password")
    unknown = await login(api, "nobody@example.com")

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["error"]["code"] == "invalid_credentials"
    assert wrong.headers["www-authenticate"] == unknown.headers["www-authenticate"] == "Bearer"
    assert "access_token" not in wrong.text


async def test_unknown_email_takes_comparable_time_to_a_wrong_password(
    api: httpx.AsyncClient,
) -> None:
    await register(api, "ana@example.com")
    await login(api, "nobody@example.com")  # warm up: computes the dummy hash

    async def timed(email: str, password: str) -> float:
        started = time.perf_counter()
        await login(api, email, password)
        return time.perf_counter() - started

    wrong = [await timed("ana@example.com", "not-the-right-password") for _ in range(4)]
    unknown = [await timed("nobody@example.com", "not-the-right-password") for _ in range(4)]

    # Without the dummy verification the unknown-email path would skip Argon2 entirely and be
    # one or two orders of magnitude faster. Allow generous noise.
    assert statistics.median(unknown) > 0.4 * statistics.median(wrong)


async def test_credentials_and_tokens_never_reach_the_logs(
    api: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_password = "this-password-must-never-be-logged"
    await register(api, "ana@example.com", secret_password)
    response = await login(api, "ana@example.com", secret_password)
    await login(api, "ana@example.com", "wrong-and-also-secret")
    body = response.json()

    logged = capsys.readouterr().out

    assert secret_password not in logged
    assert "wrong-and-also-secret" not in logged
    assert body["access_token"] not in logged
    assert body["refresh_token"] not in logged


# --- error body --------------------------------------------------------------------------------


async def test_unknown_routes_use_the_common_error_body(api: httpx.AsyncClient) -> None:
    response = await api.get("/api/v1/nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_openapi_documents_the_auth_endpoints_and_error_model(
    api: httpx.AsyncClient,
) -> None:
    schema = (await api.get("/openapi.json")).json()

    assert schema["paths"]["/api/v1/auth/register"]["post"]["responses"]["202"]
    login_responses = schema["paths"]["/api/v1/auth/login"]["post"]["responses"]
    assert {"200", "401", "422"} <= set(login_responses)
    assert "ErrorResponse" in schema["components"]["schemas"]

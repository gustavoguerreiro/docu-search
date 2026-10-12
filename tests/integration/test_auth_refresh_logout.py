import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from docusearch.core.settings import Settings
from docusearch.core.tokens import decode_access_token
from docusearch.db import collections
from docusearch.db.mongo import MongoDatabase
from docusearch.main import create_app

PASSWORD = "a-reasonably-long-password"
BASE = "/api/v1/auth"


@pytest.fixture
async def api(
    mongo_settings: Settings, mongo_db: MongoDatabase
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(mongo_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def sign_up_and_log_in(
    api: httpx.AsyncClient, email: str = "ana@example.com"
) -> dict[str, str]:
    await api.post(f"{BASE}/register", json={"email": email, "password": PASSWORD})
    response = await api.post(f"{BASE}/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    body: dict[str, str] = response.json()
    return body


async def refresh(api: httpx.AsyncClient, token: str) -> httpx.Response:
    return await api.post(f"{BASE}/refresh", json={"refresh_token": token})


async def logout(api: httpx.AsyncClient, token: str) -> httpx.Response:
    return await api.post(f"{BASE}/logout", json={"refresh_token": token})


# --- refresh -----------------------------------------------------------------------------------


async def test_refresh_returns_a_new_pair_for_the_same_user(
    api: httpx.AsyncClient, mongo_settings: Settings
) -> None:
    first = await sign_up_and_log_in(api)

    response = await refresh(api, first["refresh_token"])

    assert response.status_code == 200
    second = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert second["refresh_token"] != first["refresh_token"]
    assert second["access_token"] != first["access_token"]
    assert (
        decode_access_token(second["access_token"], mongo_settings).user_id
        == decode_access_token(first["access_token"], mongo_settings).user_id
    )


async def test_a_refresh_token_works_once(api: httpx.AsyncClient) -> None:
    first = await sign_up_and_log_in(api)
    assert (await refresh(api, first["refresh_token"])).status_code == 200

    replay = await refresh(api, first["refresh_token"])

    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "invalid_refresh_token"
    assert replay.headers["www-authenticate"] == "Bearer"


async def test_replaying_a_used_token_also_kills_the_newest_one(api: httpx.AsyncClient) -> None:
    first = await sign_up_and_log_in(api)
    newest = (await refresh(api, first["refresh_token"])).json()

    assert (await refresh(api, first["refresh_token"])).status_code == 401  # replay = theft signal

    assert (await refresh(api, newest["refresh_token"])).status_code == 401  # session is gone


async def test_the_chain_keeps_working_when_each_token_is_used_once(api: httpx.AsyncClient) -> None:
    current = (await sign_up_and_log_in(api))["refresh_token"]

    for _ in range(4):
        response = await refresh(api, current)
        assert response.status_code == 200
        current = response.json()["refresh_token"]


async def test_unknown_malformed_and_missing_tokens_fail_the_same_way(
    api: httpx.AsyncClient,
) -> None:
    unknown = await refresh(api, "a" * 43)
    malformed = await refresh(api, "not a token!")

    assert unknown.status_code == malformed.status_code == 401
    assert unknown.json() == malformed.json()
    missing = await api.post(f"{BASE}/refresh", json={})
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "validation_error"


async def test_an_access_token_is_not_accepted_as_a_refresh_token(api: httpx.AsyncClient) -> None:
    session = await sign_up_and_log_in(api)

    response = await refresh(api, session["access_token"])

    assert response.status_code == 401


async def test_refreshing_for_a_deleted_user_fails_and_ends_their_sessions(
    api: httpx.AsyncClient, mongo_db: MongoDatabase
) -> None:
    session = await sign_up_and_log_in(api)
    await mongo_db[collections.USERS].delete_many({})

    response = await refresh(api, session["refresh_token"])

    assert response.status_code == 401
    live = await mongo_db[collections.REFRESH_TOKENS].count_documents({"revoked_at": None})
    assert live == 0


async def test_concurrent_refreshes_of_one_token_do_not_both_succeed(
    api: httpx.AsyncClient,
) -> None:
    session = await sign_up_and_log_in(api)

    responses = await asyncio.gather(*(refresh(api, session["refresh_token"]) for _ in range(6)))

    codes = [r.status_code for r in responses]
    assert codes.count(200) <= 1
    assert set(codes) <= {200, 401}


# --- logout ------------------------------------------------------------------------------------


async def test_logout_ends_the_session_and_is_idempotent(api: httpx.AsyncClient) -> None:
    session = await sign_up_and_log_in(api)

    first = await logout(api, session["refresh_token"])
    second = await logout(api, session["refresh_token"])

    assert first.status_code == second.status_code == 204
    assert first.content == b""
    assert (await refresh(api, session["refresh_token"])).status_code == 401


async def test_logout_with_an_unknown_token_looks_the_same(api: httpx.AsyncClient) -> None:
    known = await sign_up_and_log_in(api)
    valid = await logout(api, known["refresh_token"])
    unknown = await logout(api, "z" * 43)
    malformed = await logout(api, "not a token!")

    assert valid.status_code == unknown.status_code == malformed.status_code == 204
    assert valid.content == unknown.content == malformed.content


async def test_logging_out_one_session_leaves_the_users_other_sessions_alone(
    api: httpx.AsyncClient,
) -> None:
    laptop = await sign_up_and_log_in(api)
    phone = (
        await api.post(f"{BASE}/login", json={"email": "ana@example.com", "password": PASSWORD})
    ).json()

    await logout(api, laptop["refresh_token"])

    assert (await refresh(api, laptop["refresh_token"])).status_code == 401
    assert (await refresh(api, phone["refresh_token"])).status_code == 200


async def test_logout_after_a_rotation_ends_the_whole_session(api: httpx.AsyncClient) -> None:
    first = await sign_up_and_log_in(api)
    newest = (await refresh(api, first["refresh_token"])).json()

    await logout(api, newest["refresh_token"])

    assert (await refresh(api, newest["refresh_token"])).status_code == 401


async def test_refresh_tokens_are_never_stored_raw(
    api: httpx.AsyncClient, mongo_db: MongoDatabase
) -> None:
    session = await sign_up_and_log_in(api)
    rotated = (await refresh(api, session["refresh_token"])).json()

    stored = [doc async for doc in mongo_db[collections.REFRESH_TOKENS].find({})]

    assert len(stored) == 2
    raw = {session["refresh_token"], rotated["refresh_token"]}
    assert not raw & {str(value) for doc in stored for value in doc.values()}

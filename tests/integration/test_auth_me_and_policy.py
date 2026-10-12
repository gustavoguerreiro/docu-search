from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import jwt
import pytest
from bson import ObjectId
from fastapi import APIRouter, FastAPI, HTTPException

from docusearch.api.dependencies import CurrentUser
from docusearch.core.clock import utc_now
from docusearch.core.settings import Settings
from docusearch.core.tokens import create_access_token
from docusearch.db import collections
from docusearch.db.ids import ObjectIdStr, parse_object_id
from docusearch.db.mongo import MongoDatabase
from docusearch.db.repositories.documents import DocumentRepository
from docusearch.main import create_app

PASSWORD = "a-reasonably-long-password"
BASE = "/api/v1/auth"


def build_app(settings: Settings) -> FastAPI:
    """The real app plus one throwaway route that follows the pattern every resource route must:
    the owner is the authenticated user, never something the client sends."""
    app = create_app(settings)
    probe = APIRouter()

    @probe.get("/api/v1/_probe/documents/{document_id}")
    async def read_document(document_id: ObjectIdStr, user: CurrentUser) -> dict[str, str]:
        repo = DocumentRepository(app.state.mongo_db)
        found = await repo.get(owner_id=user.id, document_id=parse_object_id(document_id))
        if found is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        return {"id": str(found.id), "filename": found.filename}

    @probe.get("/api/v1/_probe/documents")
    async def list_documents(user: CurrentUser) -> list[str]:
        repo = DocumentRepository(app.state.mongo_db)
        return [d.filename for d in await repo.list_by_owner(user.id)]

    app.include_router(probe)
    return app


@pytest.fixture
async def app(mongo_settings: Settings, mongo_db: MongoDatabase) -> AsyncIterator[FastAPI]:
    application = build_app(mongo_settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
def api(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def sign_up(api: httpx.AsyncClient, email: str) -> dict[str, Any]:
    await api.post(f"{BASE}/register", json={"email": email, "password": PASSWORD})
    response = await api.post(f"{BASE}/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- /auth/me ----------------------------------------------------------------------------------


async def test_me_returns_the_authenticated_account_and_nothing_secret(
    api: httpx.AsyncClient, mongo_db: MongoDatabase
) -> None:
    session = await sign_up(api, "Ana@Example.com")

    response = await api.get(f"{BASE}/me", headers=bearer(session["access_token"]))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "email", "created_at"}
    assert body["email"] == "ana@example.com"
    stored = await mongo_db[collections.USERS].find_one({"email": "ana@example.com"})
    assert stored is not None
    assert body["id"] == str(stored["_id"])
    assert response.headers["cache-control"] == "no-store"
    assert "password" not in response.text
    assert "argon2" not in response.text


async def test_me_is_documented_with_a_bearer_security_scheme(api: httpx.AsyncClient) -> None:
    schema = (await api.get("/openapi.json")).json()

    operation = schema["paths"]["/api/v1/auth/me"]["get"]
    assert {"200", "401"} <= set(operation["responses"])
    assert operation["security"]
    assert any(
        scheme.get("type") == "http" and scheme.get("scheme") == "bearer"
        for scheme in schema["components"]["securitySchemes"].values()
    )


# --- credentials that must be refused ----------------------------------------------------------


async def test_missing_credentials_get_a_401_with_a_challenge(api: httpx.AsyncClient) -> None:
    response = await api.get(f"{BASE}/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header",
    ["Basic YW5hOnBhc3M=", "Bearer", "Bearer ", "Token abc", "bearer-without-space"],
)
async def test_wrong_authorization_schemes_are_refused(api: httpx.AsyncClient, header: str) -> None:
    response = await api.get(f"{BASE}/me", headers={"Authorization": header})

    assert response.status_code == 401
    assert response.json()["error"]["code"] in {"not_authenticated", "invalid_token"}


@pytest.mark.parametrize("token", ["garbage", "a.b.c", "x" * 3000, "eyJhbGciOiJub25lIn0.e30."])
async def test_malformed_tokens_are_refused_as_invalid(api: httpx.AsyncClient, token: str) -> None:
    response = await api.get(f"{BASE}/me", headers=bearer(token))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_expired_tokens_say_so_so_clients_can_refresh(
    api: httpx.AsyncClient, mongo_settings: Settings, mongo_db: MongoDatabase
) -> None:
    await sign_up(api, "ana@example.com")
    user = await mongo_db[collections.USERS].find_one({})
    assert user is not None
    token = create_access_token(user["_id"], mongo_settings, now=utc_now() - timedelta(hours=2))

    response = await api.get(f"{BASE}/me", headers=bearer(token))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


async def test_a_token_signed_with_another_key_is_refused(
    api: httpx.AsyncClient, mongo_db: MongoDatabase
) -> None:
    await sign_up(api, "ana@example.com")
    user = await mongo_db[collections.USERS].find_one({})
    assert user is not None
    now = utc_now()
    forged = jwt.encode(
        {
            "sub": str(user["_id"]),
            "iss": "docusearch",
            "aud": "docusearch-api",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": "forged",
        },
        "an-attackers-own-key-of-sufficient-length",
        algorithm="HS256",
    )

    response = await api.get(f"{BASE}/me", headers=bearer(forged))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_a_refresh_token_is_not_an_access_token(api: httpx.AsyncClient) -> None:
    session = await sign_up(api, "ana@example.com")

    response = await api.get(f"{BASE}/me", headers=bearer(session["refresh_token"]))

    assert response.status_code == 401


async def test_a_valid_token_for_a_deleted_account_stops_working_immediately(
    api: httpx.AsyncClient, mongo_db: MongoDatabase
) -> None:
    session = await sign_up(api, "ana@example.com")
    assert (await api.get(f"{BASE}/me", headers=bearer(session["access_token"]))).status_code == 200

    await mongo_db[collections.USERS].delete_many({})

    response = await api.get(f"{BASE}/me", headers=bearer(session["access_token"]))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_logout_does_not_revoke_an_already_issued_access_token(
    api: httpx.AsyncClient,
) -> None:
    """Pins the documented limitation (ADR-0018): access tokens live until they expire."""
    session = await sign_up(api, "ana@example.com")
    await api.post(f"{BASE}/logout", json={"refresh_token": session["refresh_token"]})

    response = await api.get(f"{BASE}/me", headers=bearer(session["access_token"]))

    assert response.status_code == 200


# --- who is the caller? only the token decides -------------------------------------------------


async def test_each_user_sees_only_their_own_identity(api: httpx.AsyncClient) -> None:
    ana = await sign_up(api, "ana@example.com")
    bia = await sign_up(api, "bia@example.com")

    as_ana = await api.get(f"{BASE}/me", headers=bearer(ana["access_token"]))
    as_bia = await api.get(f"{BASE}/me", headers=bearer(bia["access_token"]))

    assert as_ana.json()["email"] == "ana@example.com"
    assert as_bia.json()["email"] == "bia@example.com"
    assert as_ana.json()["id"] != as_bia.json()["id"]


@pytest.mark.parametrize(
    ("headers", "params"),
    [
        ({"X-User-Id": "{bia}"}, {}),
        ({"X-Forwarded-User": "{bia}"}, {}),
        ({"X-Owner-Id": "{bia}"}, {}),
        ({}, {"user_id": "{bia}"}),
        ({}, {"owner_id": "{bia}"}),
    ],
)
async def test_client_supplied_identity_is_ignored(
    api: httpx.AsyncClient, headers: dict[str, str], params: dict[str, str]
) -> None:
    ana = await sign_up(api, "ana@example.com")
    bia = await sign_up(api, "bia@example.com")
    bia_id = (await api.get(f"{BASE}/me", headers=bearer(bia["access_token"]))).json()["id"]
    headers = {k: v.format(bia=bia_id) for k, v in headers.items()}
    params = {k: v.format(bia=bia_id) for k, v in params.items()}

    response = await api.get(
        f"{BASE}/me", headers={**bearer(ana["access_token"]), **headers}, params=params
    )

    assert response.json()["email"] == "ana@example.com"


# --- the ownership pattern, end to end ---------------------------------------------------------


async def test_a_resource_of_another_user_is_indistinguishable_from_a_missing_one(
    api: httpx.AsyncClient, app: FastAPI
) -> None:
    ana = await sign_up(api, "ana@example.com")
    bia = await sign_up(api, "bia@example.com")
    ana_id = (await api.get(f"{BASE}/me", headers=bearer(ana["access_token"]))).json()["id"]
    created = await DocumentRepository(app.state.mongo_db).create(
        owner_id=parse_object_id(ana_id),
        filename="ana-private.pdf",
        content_type="application/pdf",
        size_bytes=1,
        sha256="a" * 64,
        storage_key="k",
    )
    url = f"/api/v1/_probe/documents/{created.id}"

    owner = await api.get(url, headers=bearer(ana["access_token"]))
    other = await api.get(url, headers=bearer(bia["access_token"]))
    missing = await api.get(
        f"/api/v1/_probe/documents/{ObjectId()}", headers=bearer(bia["access_token"])
    )
    anonymous = await api.get(url)

    assert owner.status_code == 200
    assert owner.json()["filename"] == "ana-private.pdf"
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()
    assert "ana-private" not in other.text
    assert anonymous.status_code == 401


async def test_listings_contain_only_the_callers_resources(
    api: httpx.AsyncClient, app: FastAPI
) -> None:
    ana = await sign_up(api, "ana@example.com")
    bia = await sign_up(api, "bia@example.com")
    repo = DocumentRepository(app.state.mongo_db)
    for token, name, digest in (
        (ana, "ana-1.pdf", "1"),
        (ana, "ana-2.pdf", "2"),
        (bia, "bia-1.pdf", "3"),
    ):
        me = (await api.get(f"{BASE}/me", headers=bearer(token["access_token"]))).json()
        await repo.create(
            owner_id=parse_object_id(me["id"]),
            filename=name,
            content_type="application/pdf",
            size_bytes=1,
            sha256=digest * 64,
            storage_key=f"k{digest}",
        )

    ana_list = await api.get("/api/v1/_probe/documents", headers=bearer(ana["access_token"]))
    bia_list = await api.get("/api/v1/_probe/documents", headers=bearer(bia["access_token"]))

    assert sorted(ana_list.json()) == ["ana-1.pdf", "ana-2.pdf"]
    assert bia_list.json() == ["bia-1.pdf"]

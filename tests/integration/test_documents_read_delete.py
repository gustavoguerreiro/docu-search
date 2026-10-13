import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from bson import ObjectId

from docusearch.core.settings import Settings
from docusearch.db import collections
from docusearch.db.mongo import MongoDatabase
from docusearch.main import create_app

PASSWORD = "a-reasonably-long-password"
AUTH = "/api/v1/auth"
DOCS = "/api/v1/documents"


def pdf(tag: str) -> bytes:
    return f"%PDF-1.7\n% {tag}\n%%EOF\n".encode()


@pytest.fixture
async def app_state(
    mongo_settings: Settings, mongo_db: MongoDatabase
) -> AsyncIterator[tuple[httpx.AsyncClient, Settings, MongoDatabase]]:
    app = create_app(mongo_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, mongo_settings, mongo_db


@pytest.fixture
def api(app_state: tuple[httpx.AsyncClient, Settings, MongoDatabase]) -> httpx.AsyncClient:
    return app_state[0]


@pytest.fixture
def settings(app_state: tuple[httpx.AsyncClient, Settings, MongoDatabase]) -> Settings:
    return app_state[1]


@pytest.fixture
def db(app_state: tuple[httpx.AsyncClient, Settings, MongoDatabase]) -> MongoDatabase:
    return app_state[2]


async def sign_up(api: httpx.AsyncClient, email: str) -> dict[str, str]:
    await api.post(f"{AUTH}/register", json={"email": email, "password": PASSWORD})
    response = await api.post(f"{AUTH}/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def upload(api: httpx.AsyncClient, headers: dict[str, str], content: bytes) -> dict[str, str]:
    response = await api.post(
        DOCS, headers=headers, files={"file": ("f.pdf", content, "application/pdf")}
    )
    assert response.status_code == 202, response.text
    body: dict[str, str] = response.json()
    return body


def stored_files(settings: Settings) -> list[Path]:
    root = settings.storage_local_root
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


# --- listing -----------------------------------------------------------------------------------


async def test_listing_shows_only_my_documents_newest_first(api: httpx.AsyncClient) -> None:
    ana = await sign_up(api, "ana@example.com")
    bia = await sign_up(api, "bia@example.com")
    first = await upload(api, ana, pdf("1"))
    await asyncio.sleep(0.01)
    second = await upload(api, ana, pdf("2"))
    await upload(api, bia, pdf("3"))

    response = await api.get(DOCS, headers=ana)

    assert response.status_code == 200
    body = response.json()
    assert [d["id"] for d in body["items"]] == [second["id"], first["id"]]
    assert body["total"] == 2
    assert (body["limit"], body["offset"]) == (20, 0)
    assert response.headers["cache-control"] == "no-store"
    assert "storage_key" not in response.text


async def test_listing_paginates_without_overlap(api: httpx.AsyncClient) -> None:
    ana = await sign_up(api, "ana@example.com")
    created = [await upload(api, ana, pdf(str(i))) for i in range(5)]

    page1 = (await api.get(DOCS, headers=ana, params={"limit": 2, "offset": 0})).json()
    page2 = (await api.get(DOCS, headers=ana, params={"limit": 2, "offset": 2})).json()
    page3 = (await api.get(DOCS, headers=ana, params={"limit": 2, "offset": 4})).json()
    beyond = (await api.get(DOCS, headers=ana, params={"limit": 2, "offset": 10})).json()

    ids = [d["id"] for page in (page1, page2, page3) for d in page["items"]]
    assert [len(p["items"]) for p in (page1, page2, page3)] == [2, 2, 1]
    assert len(ids) == len(set(ids)) == 5
    assert set(ids) == {d["id"] for d in created}
    assert page1["total"] == page3["total"] == beyond["total"] == 5
    assert beyond["items"] == []


async def test_a_user_with_no_documents_gets_an_empty_page(api: httpx.AsyncClient) -> None:
    ana = await sign_up(api, "ana@example.com")

    response = await api.get(DOCS, headers=ana)

    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"limit": -1}, {"offset": -1}, {"limit": "many"}]
)
async def test_invalid_paging_is_a_validation_error(
    api: httpx.AsyncClient, params: dict[str, object]
) -> None:
    ana = await sign_up(api, "ana@example.com")

    response = await api.get(DOCS, headers=ana, params=params)  # type: ignore[arg-type]

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# --- detail ------------------------------------------------------------------------------------


async def test_my_document_can_be_read(api: httpx.AsyncClient) -> None:
    ana = await sign_up(api, "ana@example.com")
    created = await upload(api, ana, pdf("mine"))

    response = await api.get(f"{DOCS}/{created['id']}", headers=ana)

    assert response.status_code == 200
    assert response.json() == created


async def test_unknown_and_foreign_documents_are_indistinguishable(api: httpx.AsyncClient) -> None:
    ana = await sign_up(api, "ana@example.com")
    bia = await sign_up(api, "bia@example.com")
    private = await upload(api, ana, pdf("ana-private"))

    foreign = await api.get(f"{DOCS}/{private['id']}", headers=bia)
    missing = await api.get(f"{DOCS}/{ObjectId()}", headers=bia)

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert foreign.json() == {
        "error": {"code": "document_not_found", "message": "Document not found."}
    }
    assert "ana-private" not in foreign.text


@pytest.mark.parametrize("bad_id", ["not-an-id", "123", "z" * 24, "g" * 23, "0" * 25])
async def test_a_malformed_id_is_a_validation_error_not_a_lookup(
    api: httpx.AsyncClient, bad_id: str
) -> None:
    ana = await sign_up(api, "ana@example.com")

    get = await api.get(f"{DOCS}/{bad_id}", headers=ana)
    delete = await api.delete(f"{DOCS}/{bad_id}", headers=ana)

    for response in (get, delete):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


async def test_a_path_traversal_attempt_never_reaches_a_document(
    api: httpx.AsyncClient, settings: Settings
) -> None:
    ana = await sign_up(api, "ana@example.com")
    await upload(api, ana, pdf("keep-me"))

    for path in ("%2e%2e%2f%2e%2e%2fetc%2fpasswd", "..%2f..%2fetc", "%00"):
        response = await api.delete(f"{DOCS}/{path}", headers=ana)
        assert response.status_code in {404, 422}

    assert len(stored_files(settings)) == 1
    assert (await api.get(DOCS, headers=ana)).json()["total"] == 1


async def test_every_document_route_requires_authentication(api: httpx.AsyncClient) -> None:
    ana = await sign_up(api, "ana@example.com")
    created = await upload(api, ana, pdf("x"))

    responses = [
        await api.get(DOCS),
        await api.get(f"{DOCS}/{created['id']}"),
        await api.delete(f"{DOCS}/{created['id']}"),
        await api.get(DOCS, headers={"Authorization": "Bearer garbage"}),
    ]

    assert [r.status_code for r in responses] == [401, 401, 401, 401]


# --- deletion ----------------------------------------------------------------------------------


async def test_delete_removes_record_chunks_and_file_and_frees_the_content(
    api: httpx.AsyncClient, settings: Settings, db: MongoDatabase
) -> None:
    ana = await sign_up(api, "ana@example.com")
    created = await upload(api, ana, pdf("to-delete"))
    await db[collections.CHUNKS].insert_many(
        [
            {
                "document_id": ObjectId(created["id"]),
                "owner_id": ObjectId(),
                "chunk_index": i,
                "text": "t",
            }
            for i in range(3)
        ]
    )
    assert len(stored_files(settings)) == 1

    response = await api.delete(f"{DOCS}/{created['id']}", headers=ana)

    assert response.status_code == 204
    assert response.content == b""
    assert (await api.get(f"{DOCS}/{created['id']}", headers=ana)).status_code == 404
    assert stored_files(settings) == []
    assert await db[collections.CHUNKS].count_documents({}) == 0
    again = await api.post(
        DOCS, headers=ana, files={"file": ("f.pdf", pdf("to-delete"), "application/pdf")}
    )
    assert again.status_code == 202  # the content can be uploaded again


async def test_deleting_twice_is_a_404_the_second_time(api: httpx.AsyncClient) -> None:
    ana = await sign_up(api, "ana@example.com")
    created = await upload(api, ana, pdf("once"))

    first = await api.delete(f"{DOCS}/{created['id']}", headers=ana)
    second = await api.delete(f"{DOCS}/{created['id']}", headers=ana)

    assert (first.status_code, second.status_code) == (204, 404)


async def test_a_user_cannot_delete_someone_elses_document(
    api: httpx.AsyncClient, settings: Settings, db: MongoDatabase
) -> None:
    ana = await sign_up(api, "ana@example.com")
    bia = await sign_up(api, "bia@example.com")
    private = await upload(api, ana, pdf("ana-only"))

    response = await api.delete(f"{DOCS}/{private['id']}", headers=bia)

    assert response.status_code == 404
    assert (await api.get(f"{DOCS}/{private['id']}", headers=ana)).status_code == 200
    assert len(stored_files(settings)) == 1
    assert await db[collections.DOCUMENTS].count_documents({}) == 1


async def test_concurrent_deletes_succeed_exactly_once(api: httpx.AsyncClient) -> None:
    ana = await sign_up(api, "ana@example.com")
    created = await upload(api, ana, pdf("race"))

    responses = await asyncio.gather(
        *(api.delete(f"{DOCS}/{created['id']}", headers=ana) for _ in range(6))
    )

    codes = [r.status_code for r in responses]
    assert codes.count(204) == 1
    assert codes.count(404) == 5


async def test_deleting_when_the_file_is_already_gone_still_succeeds(
    api: httpx.AsyncClient, settings: Settings
) -> None:
    ana = await sign_up(api, "ana@example.com")
    created = await upload(api, ana, pdf("orphaned-record"))
    for path in stored_files(settings):
        path.unlink()  # e.g. removed by an operator or lost with a disk

    response = await api.delete(f"{DOCS}/{created['id']}", headers=ana)

    assert response.status_code == 204


# --- the whole cross-user matrix, in one place -------------------------------------------------


async def test_another_user_can_neither_see_nor_touch_my_documents(
    api: httpx.AsyncClient, settings: Settings, db: MongoDatabase
) -> None:
    ana = await sign_up(api, "ana@example.com")
    mallory = await sign_up(api, "mallory@example.com")
    mine = await upload(api, ana, pdf("ana-secret"))
    ana_id = (await api.get(f"{AUTH}/me", headers=ana)).json()["id"]

    attempts = [
        await api.get(f"{DOCS}/{mine['id']}", headers=mallory),
        await api.delete(f"{DOCS}/{mine['id']}", headers=mallory),
        # forged ownership hints must not matter
        await api.get(f"{DOCS}/{mine['id']}", headers={**mallory, "X-User-Id": ana_id}),
        await api.get(f"{DOCS}/{mine['id']}", headers=mallory, params={"owner_id": ana_id}),
        await api.delete(f"{DOCS}/{mine['id']}", headers={**mallory, "X-Owner-Id": ana_id}),
    ]
    listing = await api.get(DOCS, headers=mallory, params={"owner_id": ana_id, "user_id": ana_id})

    assert [a.status_code for a in attempts] == [404] * 5
    assert all("ana-secret" not in a.text for a in attempts)
    assert listing.json()["items"] == []
    assert listing.json()["total"] == 0
    # ...and nothing of Ana's changed
    assert (await api.get(f"{DOCS}/{mine['id']}", headers=ana)).status_code == 200
    assert len(stored_files(settings)) == 1
    assert await db[collections.DOCUMENTS].count_documents({}) == 1


async def test_openapi_documents_the_document_endpoints(api: httpx.AsyncClient) -> None:
    schema = (await api.get("/openapi.json")).json()

    paths = schema["paths"]
    assert {"get", "post"} <= set(paths["/api/v1/documents"])
    assert {"get", "delete"} <= set(paths["/api/v1/documents/{document_id}"])
    assert "404" in paths["/api/v1/documents/{document_id}"]["delete"]["responses"]
    assert "DocumentResponse" in schema["components"]["schemas"]
    assert "storage_key" not in str(schema["components"]["schemas"]["DocumentResponse"])

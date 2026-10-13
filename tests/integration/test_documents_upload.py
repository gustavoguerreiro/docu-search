import asyncio
import hashlib
import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from docusearch.core.settings import Settings
from docusearch.db import collections
from docusearch.db.mongo import MongoDatabase
from docusearch.main import create_app

PASSWORD = "a-reasonably-long-password"
AUTH = "/api/v1/auth"
DOCS = "/api/v1/documents"
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
TEXT = "Olá, mundo.\nSegunda linha.\n".encode()


def make_docx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()


@pytest.fixture
def small_settings(mongo_settings: Settings) -> Settings:
    return mongo_settings.model_copy(update={"max_upload_size_mb": 1})


@pytest.fixture
async def api(
    small_settings: Settings, mongo_db: MongoDatabase
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(small_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def sign_up(api: httpx.AsyncClient, email: str) -> dict[str, str]:
    await api.post(f"{AUTH}/register", json={"email": email, "password": PASSWORD})
    response = await api.post(f"{AUTH}/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def upload(
    api: httpx.AsyncClient,
    headers: dict[str, str],
    content: bytes,
    *,
    filename: str = "file.bin",
    content_type: str = "application/octet-stream",
) -> httpx.Response:
    return await api.post(DOCS, headers=headers, files={"file": (filename, content, content_type)})


def stored_files(settings: Settings) -> list[Path]:
    root = settings.storage_local_root
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.is_file()]


# --- success -----------------------------------------------------------------------------------


async def test_a_pdf_is_accepted_stored_and_recorded(
    api: httpx.AsyncClient, small_settings: Settings, mongo_db: MongoDatabase
) -> None:
    ana = await sign_up(api, "ana@example.com")

    response = await upload(
        api, ana, PDF, filename="Contrato 2026.pdf", content_type="application/pdf"
    )

    assert response.status_code == 202
    body = response.json()
    assert set(body) == {
        "id",
        "filename",
        "content_type",
        "size_bytes",
        "sha256",
        "status",
        "created_at",
        "updated_at",
    }
    assert body["filename"] == "Contrato 2026.pdf"
    assert body["content_type"] == "application/pdf"
    assert body["size_bytes"] == len(PDF)
    assert body["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert body["status"] == "pending"

    record = await mongo_db[collections.DOCUMENTS].find_one({})
    assert record is not None
    files = stored_files(small_settings)
    assert len(files) == 1
    assert files[0].read_bytes() == PDF  # exactly what was sent
    assert (
        files[0].relative_to(small_settings.storage_local_root).as_posix() == record["storage_key"]
    )


async def test_docx_and_text_are_accepted_with_their_detected_types(
    api: httpx.AsyncClient,
) -> None:
    ana = await sign_up(api, "ana@example.com")

    docx = await upload(api, ana, make_docx(), filename="a.docx")
    text = await upload(api, ana, TEXT, filename="notes.txt")

    assert docx.status_code == text.status_code == 202
    assert docx.json()["content_type"].endswith("wordprocessingml.document")
    assert text.json()["content_type"] == "text/plain"


async def test_the_response_never_exposes_storage_details(
    api: httpx.AsyncClient, small_settings: Settings, mongo_db: MongoDatabase
) -> None:
    ana = await sign_up(api, "ana@example.com")

    response = await upload(api, ana, PDF)

    record = await mongo_db[collections.DOCUMENTS].find_one({})
    assert record is not None
    assert "storage_key" not in response.json()
    assert record["storage_key"] not in response.text
    assert str(small_settings.storage_local_root) not in response.text
    assert "owner_id" not in response.text


# --- what the client says is not trusted -------------------------------------------------------


async def test_the_declared_name_and_type_do_not_decide_what_a_file_is(
    api: httpx.AsyncClient,
) -> None:
    ana = await sign_up(api, "ana@example.com")

    pdf_called_png = await upload(api, ana, PDF, filename="holiday.png", content_type="image/png")
    text_called_pdf = await upload(
        api, ana, TEXT, filename="report.pdf", content_type="application/pdf"
    )

    assert pdf_called_png.status_code == 202
    assert pdf_called_png.json()["content_type"] == "application/pdf"
    assert text_called_pdf.status_code == 202
    assert text_called_pdf.json()["content_type"] == "text/plain"


async def test_an_executable_renamed_to_pdf_is_refused(
    api: httpx.AsyncClient, small_settings: Settings
) -> None:
    ana = await sign_up(api, "ana@example.com")

    response = await upload(
        api,
        ana,
        b"MZ\x90\x00" + b"\x01" * 200,
        filename="invoice.pdf",
        content_type="application/pdf",
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_file_type"
    assert stored_files(small_settings) == []


async def test_hostile_file_names_are_neutralized(api: httpx.AsyncClient) -> None:
    ana = await sign_up(api, "ana@example.com")

    response = await upload(api, ana, PDF, filename="../../etc/passwd‮.pdf")

    assert response.status_code == 202
    assert "/" not in response.json()["filename"]
    assert ".." not in response.json()["filename"]
    assert "‮" not in response.json()["filename"]


# --- limits and validation ---------------------------------------------------------------------


async def test_an_oversized_file_is_refused_and_leaves_nothing_behind(
    api: httpx.AsyncClient, small_settings: Settings, mongo_db: MongoDatabase
) -> None:
    ana = await sign_up(api, "ana@example.com")
    too_big = b"%PDF-1.4\n" + b"x" * (small_settings.max_upload_size_mb * 1024 * 1024)

    response = await upload(api, ana, too_big)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert stored_files(small_settings) == []
    assert await mongo_db[collections.DOCUMENTS].count_documents({}) == 0


async def test_a_file_exactly_at_the_limit_is_accepted(
    api: httpx.AsyncClient, small_settings: Settings
) -> None:
    ana = await sign_up(api, "ana@example.com")
    limit = small_settings.max_upload_size_mb * 1024 * 1024
    header = b"%PDF-1.4\n"

    response = await upload(api, ana, header + b"x" * (limit - len(header)))

    assert response.status_code == 202
    assert response.json()["size_bytes"] == limit


async def test_an_empty_file_is_refused(api: httpx.AsyncClient) -> None:
    ana = await sign_up(api, "ana@example.com")

    response = await upload(api, ana, b"")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_file"


async def test_a_request_without_the_file_field_is_a_validation_error(
    api: httpx.AsyncClient,
) -> None:
    ana = await sign_up(api, "ana@example.com")

    response = await api.post(DOCS, headers=ana, files={"other": ("x.txt", b"hi", "text/plain")})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_uploading_requires_authentication(
    api: httpx.AsyncClient, small_settings: Settings
) -> None:
    anonymous = await upload(api, {}, PDF)
    forged = await upload(api, {"Authorization": "Bearer not-a-token"}, PDF)

    assert anonymous.status_code == forged.status_code == 401
    assert stored_files(small_settings) == []


# --- duplicates --------------------------------------------------------------------------------


async def test_uploading_the_same_content_twice_is_a_conflict_pointing_at_the_first(
    api: httpx.AsyncClient, small_settings: Settings, mongo_db: MongoDatabase
) -> None:
    ana = await sign_up(api, "ana@example.com")
    first = await upload(api, ana, PDF, filename="first.pdf")

    second = await upload(api, ana, PDF, filename="renamed.pdf")

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "duplicate_document"
    assert second.headers["location"] == f"/api/v1/documents/{first.json()['id']}"
    assert len(stored_files(small_settings)) == 1  # the second copy was never stored
    assert await mongo_db[collections.DOCUMENTS].count_documents({}) == 1


async def test_the_same_content_is_not_a_duplicate_across_users(
    api: httpx.AsyncClient, small_settings: Settings
) -> None:
    ana = await sign_up(api, "ana@example.com")
    bia = await sign_up(api, "bia@example.com")

    first = await upload(api, ana, PDF)
    second = await upload(api, bia, PDF)

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] != second.json()["id"]
    assert len(stored_files(small_settings)) == 2  # no cross-user sharing of stored objects


async def test_concurrent_identical_uploads_store_exactly_one_document(
    api: httpx.AsyncClient, small_settings: Settings, mongo_db: MongoDatabase
) -> None:
    ana = await sign_up(api, "ana@example.com")

    responses = await asyncio.gather(*(upload(api, ana, PDF) for _ in range(6)))

    codes = sorted(r.status_code for r in responses)
    assert codes.count(202) == 1
    assert set(codes) <= {202, 409}
    assert await mongo_db[collections.DOCUMENTS].count_documents({}) == 1
    assert len(stored_files(small_settings)) == 1  # losers cleaned up after themselves


# --- failure handling --------------------------------------------------------------------------


async def test_an_unavailable_file_store_is_reported_without_internal_details(
    mongo_settings: Settings, mongo_db: MongoDatabase, tmp_path: Path
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a file where a directory should be")
    settings = mongo_settings.model_copy(
        update={"storage_local_root": blocker / "uploads", "jwt_secret_key": SecretStr("k" * 40)}
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ana = await sign_up(client, "ana@example.com")
            response = await upload(client, ana, PDF)
            ready = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"
    assert str(blocker) not in response.text
    assert await mongo_db[collections.DOCUMENTS].count_documents({}) == 0
    assert ready.json()["checks"]["storage"] == "fail"


async def test_secrets_and_content_never_reach_the_logs(
    api: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    ana = await sign_up(api, "ana@example.com")
    secret_text = "TOP-SECRET-DOCUMENT-BODY-0451"

    await upload(api, ana, f"{secret_text}\n".encode(), filename="board-minutes.txt")

    logged: Any = capsys.readouterr().out
    assert secret_text not in logged

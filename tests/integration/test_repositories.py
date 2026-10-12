import asyncio
from datetime import timedelta

import pytest
from bson import ObjectId

from docusearch.core.clock import utc_now
from docusearch.db import collections
from docusearch.db.indexes import RETIRED_INDEXES, ensure_indexes
from docusearch.db.models import Document, DocumentStatus, NewChunk
from docusearch.db.mongo import MongoDatabase
from docusearch.db.repositories.chunks import ChunkRepository, DuplicateChunkError
from docusearch.db.repositories.documents import DocumentRepository, DuplicateDocumentError
from docusearch.db.repositories.refresh_tokens import RefreshTokenRepository
from docusearch.db.repositories.users import EmailAlreadyExistsError, UserRepository


@pytest.fixture
async def db(mongo_db: MongoDatabase) -> MongoDatabase:
    await ensure_indexes(mongo_db)
    return mongo_db


async def new_document(
    repo: DocumentRepository, owner: ObjectId, sha256: str = "a" * 64, filename: str = "a.pdf"
) -> Document:
    return await repo.create(
        owner_id=owner,
        filename=filename,
        content_type="application/pdf",
        size_bytes=123,
        sha256=sha256,
        storage_key=f"k/{sha256[:8]}",
    )


# --- users -------------------------------------------------------------------------------------


async def test_user_is_created_with_a_normalized_email_and_found_regardless_of_case(
    db: MongoDatabase,
) -> None:
    users = UserRepository(db)

    created = await users.create(email="  Ana@Example.COM ", password_hash="argon2-hash")

    assert created.email == "ana@example.com"
    assert created.created_at.tzinfo is not None
    for variant in ("ana@example.com", "ANA@EXAMPLE.COM", " ana@example.com "):
        found = await users.get_by_email(variant)
        assert found is not None
        assert found.id == created.id
    assert (await users.get_by_id(created.id)) == created


async def test_duplicate_email_is_rejected_even_when_written_differently(
    db: MongoDatabase,
) -> None:
    users = UserRepository(db)
    await users.create(email="ana@example.com", password_hash="h")

    with pytest.raises(EmailAlreadyExistsError):
        await users.create(email=" ANA@example.com", password_hash="h2")


async def test_unknown_users_are_none(db: MongoDatabase) -> None:
    users = UserRepository(db)

    assert await users.get_by_email("nobody@example.com") is None
    assert await users.get_by_id(ObjectId()) is None


async def test_concurrent_registrations_of_one_email_produce_exactly_one_user(
    db: MongoDatabase,
) -> None:
    users = UserRepository(db)

    results = await asyncio.gather(
        *(users.create(email="race@example.com", password_hash="h") for _ in range(8)),
        return_exceptions=True,
    )

    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(isinstance(r, EmailAlreadyExistsError) for r in results if isinstance(r, Exception))


# --- documents ---------------------------------------------------------------------------------


async def test_document_starts_pending_and_is_readable_only_by_its_owner(db: MongoDatabase) -> None:
    documents = DocumentRepository(db)
    ana, bia = ObjectId(), ObjectId()

    created = await new_document(documents, ana)

    assert created.status is DocumentStatus.PENDING
    assert created.owner_id == ana
    assert await documents.get(ana, created.id) == created
    assert await documents.get(bia, created.id) is None  # exists, but not hers


async def test_a_user_cannot_delete_someone_elses_document(db: MongoDatabase) -> None:
    documents = DocumentRepository(db)
    ana, bia = ObjectId(), ObjectId()
    created = await new_document(documents, ana)

    assert await documents.delete(bia, created.id) is False
    assert await documents.get(ana, created.id) is not None  # untouched

    assert await documents.delete(ana, created.id) is True
    assert await documents.get(ana, created.id) is None
    assert await documents.delete(ana, created.id) is False


async def test_same_content_is_a_duplicate_for_one_owner_but_not_across_owners(
    db: MongoDatabase,
) -> None:
    documents = DocumentRepository(db)
    ana, bia = ObjectId(), ObjectId()
    await new_document(documents, ana, sha256="b" * 64)

    with pytest.raises(DuplicateDocumentError):
        await new_document(documents, ana, sha256="b" * 64, filename="renamed.pdf")
    await new_document(documents, bia, sha256="b" * 64)


async def test_listing_is_scoped_newest_first_and_counted(db: MongoDatabase) -> None:
    documents = DocumentRepository(db)
    ana, bia = ObjectId(), ObjectId()
    first = await new_document(documents, ana, sha256="1" * 64)
    await asyncio.sleep(0.01)
    second = await new_document(documents, ana, sha256="2" * 64)
    await new_document(documents, bia, sha256="3" * 64)

    listed = await documents.list_by_owner(ana)

    assert [d.id for d in listed] == [second.id, first.id]
    assert await documents.count_by_owner(ana) == 2
    assert await documents.count_by_owner(ObjectId()) == 0
    assert await documents.list_by_owner(ObjectId()) == []


async def test_pages_neither_overlap_nor_skip_documents_that_share_a_timestamp(
    db: MongoDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen = utc_now() - timedelta(minutes=1)
    monkeypatch.setattr("docusearch.db.repositories.documents.utc_now", lambda: frozen)
    documents = DocumentRepository(db)
    owner = ObjectId()
    created = [await new_document(documents, owner, sha256=f"{i:064d}") for i in range(7)]

    pages = [await documents.list_by_owner(owner, limit=3, offset=offset) for offset in (0, 3, 6)]
    seen = [d.id for page in pages for d in page]

    assert [len(page) for page in pages] == [3, 3, 1]
    assert len(seen) == len(set(seen)) == 7
    assert set(seen) == {d.id for d in created}
    assert seen == sorted(seen, reverse=True)  # identical timestamps: ordered by _id, newest first


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (101, 0), (-1, 0), (10, -1)])
async def test_invalid_paging_is_rejected(db: MongoDatabase, limit: int, offset: int) -> None:
    with pytest.raises(ValueError, match=r"limit|offset"):
        await DocumentRepository(db).list_by_owner(ObjectId(), limit=limit, offset=offset)


# --- chunks ------------------------------------------------------------------------------------


async def test_chunks_are_returned_in_reading_order_for_their_owner_only(db: MongoDatabase) -> None:
    chunks = ChunkRepository(db)
    ana, bia, document = ObjectId(), ObjectId(), ObjectId()

    inserted = await chunks.insert_many(
        document_id=document,
        owner_id=ana,
        chunks=[NewChunk(2, "third"), NewChunk(0, "first", page=1), NewChunk(1, "second", page=1)],
    )
    listed = await chunks.list_by_document(ana, document)

    assert inserted == 3
    assert [c.text for c in listed] == ["first", "second", "third"]
    assert [c.page for c in listed] == [1, 1, None]
    assert await chunks.list_by_document(bia, document) == []


async def test_inserting_the_same_chunk_positions_twice_is_refused(db: MongoDatabase) -> None:
    chunks = ChunkRepository(db)
    owner, document = ObjectId(), ObjectId()
    batch = [NewChunk(0, "a"), NewChunk(1, "b")]
    await chunks.insert_many(document_id=document, owner_id=owner, chunks=batch)

    with pytest.raises(DuplicateChunkError):
        await chunks.insert_many(document_id=document, owner_id=owner, chunks=batch)

    assert len(await chunks.list_by_document(owner, document)) == 2  # no duplicates were written


async def test_empty_batches_and_deleting_by_document(db: MongoDatabase) -> None:
    chunks = ChunkRepository(db)
    owner, kept, removed = ObjectId(), ObjectId(), ObjectId()
    assert await chunks.insert_many(document_id=removed, owner_id=owner, chunks=[]) == 0
    await chunks.insert_many(document_id=removed, owner_id=owner, chunks=[NewChunk(0, "x")])
    await chunks.insert_many(document_id=kept, owner_id=owner, chunks=[NewChunk(0, "y")])

    assert await chunks.delete_by_document(removed) == 1

    assert await chunks.list_by_document(owner, removed) == []
    assert len(await chunks.list_by_document(owner, kept)) == 1


# --- refresh tokens ----------------------------------------------------------------------------


async def test_refresh_token_is_stored_by_hash_and_revoked_exactly_once(db: MongoDatabase) -> None:
    tokens = RefreshTokenRepository(db)
    user = ObjectId()
    expires = utc_now() + timedelta(days=14)
    created = await tokens.create(user_id=user, token_hash="hash-1", expires_at=expires)

    assert created.revoked_at is None
    assert await tokens.get_by_hash("hash-1") == created
    assert await tokens.get_by_hash("other") is None

    assert await tokens.revoke("hash-1") is True
    assert await tokens.revoke("hash-1") is False  # already revoked
    revoked = await tokens.get_by_hash("hash-1")
    assert revoked is not None
    assert revoked.revoked_at is not None
    assert revoked.revoked_at.tzinfo is not None


async def test_concurrent_revocations_succeed_for_exactly_one_caller(db: MongoDatabase) -> None:
    tokens = RefreshTokenRepository(db)
    await tokens.create(
        user_id=ObjectId(), token_hash="contested", expires_at=utc_now() + timedelta(days=1)
    )

    outcomes = await asyncio.gather(*(tokens.revoke("contested") for _ in range(8)))

    assert outcomes.count(True) == 1


async def test_revoking_all_sessions_only_touches_that_user(db: MongoDatabase) -> None:
    tokens = RefreshTokenRepository(db)
    ana, bia = ObjectId(), ObjectId()
    expires = utc_now() + timedelta(days=1)
    for name, user in (("a1", ana), ("a2", ana), ("b1", bia)):
        await tokens.create(user_id=user, token_hash=name, expires_at=expires)
    await tokens.revoke("a1")

    assert await tokens.revoke_all_for_user(ana) == 1  # a1 was already revoked

    revoked_b = await tokens.get_by_hash("b1")
    assert revoked_b is not None
    assert revoked_b.revoked_at is None


async def test_naive_expiry_is_refused(db: MongoDatabase) -> None:
    from datetime import datetime

    with pytest.raises(ValueError, match="timezone-aware"):
        await RefreshTokenRepository(db).create(
            user_id=ObjectId(),
            token_hash="h",
            expires_at=datetime(2030, 1, 1),  # noqa: DTZ001 - deliberately naive
        )


# --- index retirement --------------------------------------------------------------------------


async def test_ensure_drops_retired_indexes_but_keeps_the_replacement(
    mongo_db: MongoDatabase,
) -> None:
    documents = mongo_db[collections.DOCUMENTS]
    await documents.create_index([("owner_id", 1), ("created_at", -1)], name="owner_created")

    await ensure_indexes(mongo_db)

    names = set(await documents.index_information())
    assert "owner_created" not in names
    assert "owner_created_id" in names
    assert RETIRED_INDEXES[collections.DOCUMENTS] == ["owner_created"]

import io
from typing import IO, Any, cast

import pytest
from bson import ObjectId

from docusearch.core.clock import utc_now
from docusearch.db.models import Document, DocumentStatus
from docusearch.db.repositories.documents import DuplicateDocumentError
from docusearch.documents.intake import (
    EmptyUploadError,
    UnsupportedFileTypeError,
    UploadTooLargeError,
)
from docusearch.documents.service import DocumentAlreadyExistsError, DocumentService
from docusearch.storage import StorageError

OWNER = ObjectId()
PDF = b"%PDF-1.4\nbody\n%%EOF\n"


class BytesReader:
    def __init__(self, data: bytes) -> None:
        self._buffer = io.BytesIO(data)

    async def read(self, size: int = -1, /) -> bytes:
        return self._buffer.read(size)


def make_document(**overrides: Any) -> Document:
    now = utc_now()
    values: dict[str, Any] = {
        "_id": ObjectId(),
        "owner_id": OWNER,
        "filename": "a.pdf",
        "content_type": "application/pdf",
        "size_bytes": len(PDF),
        "sha256": "0" * 64,
        "storage_key": "ab/" + "0" * 32,
        "status": DocumentStatus.PENDING,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return Document.model_validate(values)


class FakeStorage:
    def __init__(self) -> None:
        self.stored: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail_put = False
        self.fail_delete = False

    async def put(self, key: str, source: IO[bytes]) -> int:
        if self.fail_put:
            raise StorageError("disk full")
        data = source.read()
        self.stored[key] = data
        return len(data)

    async def delete(self, key: str) -> bool:
        self.deleted.append(key)
        if self.fail_delete:
            raise StorageError("cannot delete")
        return self.stored.pop(key, None) is not None

    async def healthy(self) -> bool:
        return True


class FakeDocuments:
    def __init__(self) -> None:
        self.existing: Document | None = None
        self.created: list[dict[str, Any]] = []
        self.race: Document | None = None  # a document that appears between check and create
        self.create_error: Exception | None = None
        self.by_id: dict[ObjectId, Document] = {}
        self.lose_delete_race = False

    async def find_by_sha256(self, owner_id: ObjectId, sha256: str) -> Document | None:
        return self.existing

    async def get(self, owner_id: ObjectId, document_id: ObjectId) -> Document | None:
        found = self.by_id.get(document_id)
        return found if found is not None and found.owner_id == owner_id else None

    async def delete(self, owner_id: ObjectId, document_id: ObjectId) -> bool:
        found = self.by_id.get(document_id)
        if found is None or found.owner_id != owner_id or self.lose_delete_race:
            return False
        del self.by_id[document_id]
        return True

    async def list_by_owner(self, owner_id: ObjectId, *, limit: int, offset: int) -> list[Document]:
        mine = [d for d in self.by_id.values() if d.owner_id == owner_id]
        return mine[offset : offset + limit]

    async def count_by_owner(self, owner_id: ObjectId) -> int:
        return sum(1 for d in self.by_id.values() if d.owner_id == owner_id)

    async def create(self, **fields: Any) -> Document:
        if self.race is not None:
            self.existing = self.race
            raise DuplicateDocumentError
        if self.create_error is not None:
            raise self.create_error
        self.created.append(fields)
        return make_document(**{k: v for k, v in fields.items() if k != "owner_id"})


class FakeChunks:
    def __init__(self) -> None:
        self.deleted_for: list[ObjectId] = []
        self.fail = False

    async def delete_by_document(self, document_id: ObjectId) -> int:
        if self.fail:
            raise RuntimeError("chunk store unavailable")
        self.deleted_for.append(document_id)
        return 3


@pytest.fixture
def chunks() -> FakeChunks:
    return FakeChunks()


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def documents() -> FakeDocuments:
    return FakeDocuments()


@pytest.fixture
def service(storage: FakeStorage, documents: FakeDocuments, chunks: FakeChunks) -> DocumentService:
    return DocumentService(
        cast(Any, documents), cast(Any, chunks), storage, max_upload_bytes=10_000
    )


async def test_a_valid_upload_is_stored_then_recorded(
    service: DocumentService, storage: FakeStorage, documents: FakeDocuments
) -> None:
    document = await service.upload(OWNER, BytesReader(PDF), "../../Report 2026.pdf")

    assert len(storage.stored) == 1
    ((key, data),) = storage.stored.items()
    assert data == PDF
    (fields,) = documents.created
    assert fields["storage_key"] == key
    assert fields["owner_id"] == OWNER
    assert fields["filename"] == "Report 2026.pdf"  # sanitized
    assert fields["content_type"] == "application/pdf"  # detected, not declared
    assert fields["size_bytes"] == len(PDF)
    assert len(fields["sha256"]) == 64
    assert document.status is DocumentStatus.PENDING


async def test_a_duplicate_is_refused_before_anything_is_stored(
    service: DocumentService, storage: FakeStorage, documents: FakeDocuments
) -> None:
    documents.existing = make_document()

    with pytest.raises(DocumentAlreadyExistsError) as caught:
        await service.upload(OWNER, BytesReader(PDF), "again.pdf")

    assert caught.value.document_id == documents.existing.id
    assert storage.stored == {}
    assert documents.created == []


async def test_losing_a_race_removes_the_file_and_reports_the_winner(
    service: DocumentService, storage: FakeStorage, documents: FakeDocuments
) -> None:
    winner = make_document()
    documents.race = winner

    with pytest.raises(DocumentAlreadyExistsError) as caught:
        await service.upload(OWNER, BytesReader(PDF), "a.pdf")

    assert caught.value.document_id == winner.id
    assert storage.stored == {}  # our copy was cleaned up
    assert len(storage.deleted) == 1


async def test_a_failed_record_removes_the_stored_file_and_the_error_propagates(
    service: DocumentService, storage: FakeStorage, documents: FakeDocuments
) -> None:
    documents.create_error = RuntimeError("database exploded")

    with pytest.raises(RuntimeError, match="database exploded"):
        await service.upload(OWNER, BytesReader(PDF), "a.pdf")

    assert storage.stored == {}


async def test_a_failed_cleanup_never_masks_the_original_error(
    service: DocumentService, storage: FakeStorage, documents: FakeDocuments
) -> None:
    documents.create_error = RuntimeError("database exploded")
    storage.fail_delete = True

    with pytest.raises(RuntimeError, match="database exploded"):
        await service.upload(OWNER, BytesReader(PDF), "a.pdf")


async def test_a_storage_failure_creates_no_record(
    service: DocumentService, storage: FakeStorage, documents: FakeDocuments
) -> None:
    storage.fail_put = True

    with pytest.raises(StorageError):
        await service.upload(OWNER, BytesReader(PDF), "a.pdf")

    assert documents.created == []


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (b"", EmptyUploadError),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, UnsupportedFileTypeError),
        (b"%PDF-" + b"x" * 20_000, UploadTooLargeError),
    ],
)
async def test_rejected_uploads_touch_neither_storage_nor_database(
    service: DocumentService,
    storage: FakeStorage,
    documents: FakeDocuments,
    data: bytes,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        await service.upload(OWNER, BytesReader(data), "x.pdf")

    assert storage.stored == {}
    assert documents.created == []


# --- reading and deleting ---------------------------------------------------------------------


def stored_document(documents: FakeDocuments, storage: FakeStorage, owner: ObjectId) -> Document:
    document = make_document(owner_id=owner)
    documents.by_id[document.id] = document
    storage.stored[document.storage_key] = b"content"
    return document


async def test_get_is_scoped_to_the_owner(
    service: DocumentService, documents: FakeDocuments, storage: FakeStorage
) -> None:
    document = stored_document(documents, storage, OWNER)

    assert await service.get(OWNER, document.id) == document
    assert await service.get(ObjectId(), document.id) is None


async def test_list_returns_the_owners_page_and_total(
    service: DocumentService, documents: FakeDocuments, storage: FakeStorage
) -> None:
    for _ in range(3):
        stored_document(documents, storage, OWNER)
    stored_document(documents, storage, ObjectId())

    page, total = await service.list(OWNER, limit=2, offset=0)

    assert len(page) == 2
    assert total == 3
    assert all(d.owner_id == OWNER for d in page)


async def test_delete_removes_record_chunks_and_file(
    service: DocumentService,
    documents: FakeDocuments,
    storage: FakeStorage,
    chunks: FakeChunks,
) -> None:
    document = stored_document(documents, storage, OWNER)

    assert await service.delete(OWNER, document.id) is True

    assert document.id not in documents.by_id
    assert chunks.deleted_for == [document.id]
    assert storage.stored == {}


async def test_delete_of_someone_elses_document_changes_nothing(
    service: DocumentService,
    documents: FakeDocuments,
    storage: FakeStorage,
    chunks: FakeChunks,
) -> None:
    document = stored_document(documents, storage, OWNER)

    assert await service.delete(ObjectId(), document.id) is False

    assert document.id in documents.by_id
    assert chunks.deleted_for == []
    assert storage.stored != {}


async def test_delete_that_loses_a_race_reports_not_found_and_touches_no_files(
    service: DocumentService,
    documents: FakeDocuments,
    storage: FakeStorage,
    chunks: FakeChunks,
) -> None:
    document = stored_document(documents, storage, OWNER)
    documents.lose_delete_race = True

    assert await service.delete(OWNER, document.id) is False

    assert chunks.deleted_for == []
    assert storage.deleted == []


async def test_a_failing_chunk_cleanup_does_not_undo_or_fail_the_delete(
    service: DocumentService,
    documents: FakeDocuments,
    storage: FakeStorage,
    chunks: FakeChunks,
) -> None:
    document = stored_document(documents, storage, OWNER)
    chunks.fail = True

    assert await service.delete(OWNER, document.id) is True

    assert document.id not in documents.by_id
    assert storage.stored == {}  # the file is still removed


async def test_a_failing_file_cleanup_does_not_fail_the_delete(
    service: DocumentService, documents: FakeDocuments, storage: FakeStorage
) -> None:
    document = stored_document(documents, storage, OWNER)
    storage.fail_delete = True

    assert await service.delete(OWNER, document.id) is True

    assert document.id not in documents.by_id

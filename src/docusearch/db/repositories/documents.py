from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from docusearch.core.clock import utc_now
from docusearch.db import collections
from docusearch.db.models import Document, DocumentStatus
from docusearch.db.mongo import MongoDatabase
from docusearch.db.repositories import check_page


class DuplicateDocumentError(Exception):
    """This owner already has a document with the same content hash."""


class DocumentRepository:
    """Every operation takes the owner and filters on it in the query itself."""

    def __init__(self, db: MongoDatabase) -> None:
        self._documents = db[collections.DOCUMENTS]

    async def create(
        self,
        *,
        owner_id: ObjectId,
        filename: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        storage_key: str,
    ) -> Document:
        now = utc_now()
        document = {
            "owner_id": owner_id,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "storage_key": storage_key,
            "status": DocumentStatus.PENDING.value,
            "error_code": None,
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = await self._documents.insert_one(document)
        except DuplicateKeyError as exc:
            raise DuplicateDocumentError from exc
        return Document.model_validate({**document, "_id": result.inserted_id})

    async def get(self, owner_id: ObjectId, document_id: ObjectId) -> Document | None:
        """Another owner's document is indistinguishable from a missing one."""
        found = await self._documents.find_one({"_id": document_id, "owner_id": owner_id})
        return Document.model_validate(found) if found else None

    async def find_by_sha256(self, owner_id: ObjectId, sha256: str) -> Document | None:
        """The owner's document with this content hash, if any (duplicate detection)."""
        found = await self._documents.find_one({"owner_id": owner_id, "sha256": sha256})
        return Document.model_validate(found) if found else None

    async def list_by_owner(
        self, owner_id: ObjectId, *, limit: int = 20, offset: int = 0
    ) -> list[Document]:
        """Newest first. `_id` breaks ties so pages never overlap or skip documents that share a
        millisecond; the sort is served entirely by the `owner_created_id` index."""
        check_page(limit, offset)
        cursor = (
            self._documents.find({"owner_id": owner_id})
            .sort([("created_at", -1), ("_id", -1)])
            .skip(offset)
            .limit(limit)
        )
        return [Document.model_validate(found) async for found in cursor]

    async def count_by_owner(self, owner_id: ObjectId) -> int:
        return await self._documents.count_documents({"owner_id": owner_id})

    async def delete(self, owner_id: ObjectId, document_id: ObjectId) -> bool:
        """True if a document was deleted. Its chunks are the caller's job (not atomic)."""
        result = await self._documents.delete_one({"_id": document_id, "owner_id": owner_id})
        return result.deleted_count == 1

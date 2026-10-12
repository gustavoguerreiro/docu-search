from collections.abc import Sequence

from bson import ObjectId
from pymongo.errors import BulkWriteError

from docusearch.db import collections
from docusearch.db.models import Chunk, NewChunk
from docusearch.db.mongo import MongoDatabase
from docusearch.db.repositories import check_page

_DUPLICATE_KEY_CODE = 11000


class DuplicateChunkError(Exception):
    """A chunk with this (document, position) already exists, e.g. a job ran twice."""


class ChunkRepository:
    def __init__(self, db: MongoDatabase) -> None:
        self._chunks = db[collections.CHUNKS]

    async def insert_many(
        self, *, document_id: ObjectId, owner_id: ObjectId, chunks: Sequence[NewChunk]
    ) -> int:
        """Insert chunks; returns how many. Positions are unique per document, so inserting the same
        chunk twice raises `DuplicateChunkError` instead of creating a duplicate."""
        if not chunks:
            return 0
        documents = [
            {
                "document_id": document_id,
                "owner_id": owner_id,
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "page": chunk.page,
            }
            for chunk in chunks
        ]
        try:
            result = await self._chunks.insert_many(documents, ordered=True)
        except BulkWriteError as exc:
            errors = exc.details.get("writeErrors", [])
            if errors and all(error.get("code") == _DUPLICATE_KEY_CODE for error in errors):
                raise DuplicateChunkError from exc
            raise
        return len(result.inserted_ids)

    async def list_by_document(
        self, owner_id: ObjectId, document_id: ObjectId, *, limit: int = 50, offset: int = 0
    ) -> list[Chunk]:
        """In reading order. Filters on the owner as well as the document."""
        check_page(limit, offset)
        cursor = (
            self._chunks.find({"document_id": document_id, "owner_id": owner_id})
            .sort("chunk_index", 1)
            .skip(offset)
            .limit(limit)
        )
        return [Chunk.model_validate(found) async for found in cursor]

    async def delete_by_document(self, document_id: ObjectId) -> int:
        """Delete all chunks of a document; returns how many. Callers have already established
        ownership of the document."""
        result = await self._chunks.delete_many({"document_id": document_id})
        return result.deleted_count

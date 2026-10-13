"""Accepting a document: validate, deduplicate, store, record.

MongoDB and the file store are separate systems with no shared transaction, so the order is chosen
to make every failure safe:

1. Validate and hash the upload (nothing persisted yet).
2. Refuse a duplicate before touching storage.
3. Store the file under a new server-generated key.
4. Create the database record. If that fails, remove the file just stored.

The only unrecoverable window is a crash between steps 3 and 4, which leaves an unreferenced file;
reconciliation (Milestone 5) cleans those up. A record without a file cannot occur.
"""

import logging

from bson import ObjectId

from docusearch.db.models import Document
from docusearch.db.repositories.chunks import ChunkRepository
from docusearch.db.repositories.documents import DocumentRepository, DuplicateDocumentError
from docusearch.documents.filenames import sanitize_filename
from docusearch.documents.intake import AsyncReader, receive_upload
from docusearch.storage import Storage, StorageError, new_storage_key

logger = logging.getLogger(__name__)


class DocumentAlreadyExistsError(Exception):
    """The owner already has a document with identical content."""

    def __init__(self, document_id: ObjectId) -> None:
        super().__init__("document already exists")
        self.document_id = document_id


class DocumentService:
    def __init__(
        self,
        documents: DocumentRepository,
        chunks: ChunkRepository,
        storage: Storage,
        *,
        max_upload_bytes: int,
    ) -> None:
        self._documents = documents
        self._chunks = chunks
        self._storage = storage
        self._max_upload_bytes = max_upload_bytes

    async def get(self, owner_id: ObjectId, document_id: ObjectId) -> Document | None:
        """The owner's document, or None. Another user's document is None too."""
        return await self._documents.get(owner_id, document_id)

    async def list(
        self, owner_id: ObjectId, *, limit: int, offset: int
    ) -> tuple[list[Document], int]:
        """A page of the owner's documents, newest first, and the owner's total."""
        items = await self._documents.list_by_owner(owner_id, limit=limit, offset=offset)
        return items, await self._documents.count_by_owner(owner_id)

    async def delete(self, owner_id: ObjectId, document_id: ObjectId) -> bool:
        """Delete a document the owner owns; False if there is no such document for this owner.

        The record goes first: it is the source of truth, so from that moment the document is gone
        for the user. Chunks and the file follow; if either cleanup fails the document stays
        deleted and the leftover is logged for reconciliation (there is no cross-system
        transaction).
        """
        document = await self._documents.get(owner_id, document_id)
        if document is None:
            return False
        if not await self._documents.delete(owner_id, document_id):
            return False  # a concurrent delete won

        try:
            await self._chunks.delete_by_document(document_id)
        except Exception:
            logger.warning("could not remove chunks of a deleted document", exc_info=True)
        await self._discard(document.storage_key)
        return True

    async def upload(
        self, owner_id: ObjectId, reader: AsyncReader, filename: str | None
    ) -> Document:
        """Store an uploaded file for `owner_id`.

        Raises `UploadRejectedError` subclasses (not acceptable), `DocumentAlreadyExistsError`
        (same content already uploaded by this owner) or `StorageError` (backend failure).
        """
        received = await receive_upload(reader, max_bytes=self._max_upload_bytes)
        try:
            existing = await self._documents.find_by_sha256(owner_id, received.sha256)
            if existing is not None:
                raise DocumentAlreadyExistsError(existing.id)

            key = new_storage_key()
            await self._storage.put(key, received.file)
            try:
                return await self._documents.create(
                    owner_id=owner_id,
                    filename=sanitize_filename(filename),
                    content_type=received.document_type.content_type,
                    size_bytes=received.size,
                    sha256=received.sha256,
                    storage_key=key,
                )
            except DuplicateDocumentError:
                # Lost a race with a concurrent upload of the same content.
                await self._discard(key)
                winner = await self._documents.find_by_sha256(owner_id, received.sha256)
                if winner is None:  # the winner was deleted again in the meantime; unlikely
                    raise
                raise DocumentAlreadyExistsError(winner.id) from None
            except BaseException:
                await self._discard(key)
                raise
        finally:
            received.close()

    async def _discard(self, key: str) -> None:
        try:
            await self._storage.delete(key)
        except StorageError:
            # Nothing more can be done here; the orphan is reconciled later. Never mask the
            # original failure with this one.
            logger.warning("could not remove file after failed upload")

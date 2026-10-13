"""Receiving an upload: bounded, hashed and type-checked before anything is stored.

The upload is read in chunks and copied to a temporary file (memory up to 1 MiB, then disk) while
the size is counted and the SHA-256 computed. Reading stops as soon as the limit is exceeded, so an
oversized upload costs at most `max_bytes` of work and disk. Only a file that is non-empty, within
the limit and of an accepted type is returned.
"""

import asyncio
import hashlib
import tempfile
from dataclasses import dataclass
from typing import IO, Protocol

from docusearch.documents.filetypes import DocumentType, detect_document_type

CHUNK_SIZE = 256 * 1024
_SPOOL_MEMORY_LIMIT = 1024 * 1024


class UploadRejectedError(Exception):
    """Base class: the upload will not be accepted."""


class UploadTooLargeError(UploadRejectedError):
    pass


class EmptyUploadError(UploadRejectedError):
    pass


class UnsupportedFileTypeError(UploadRejectedError):
    pass


class AsyncReader(Protocol):
    async def read(self, size: int = -1, /) -> bytes: ...


@dataclass
class ReceivedUpload:
    """A validated upload held in a temporary file. Call `close()` when done with it."""

    file: IO[bytes]
    size: int
    sha256: str
    document_type: DocumentType

    def close(self) -> None:
        self.file.close()


async def receive_upload(reader: AsyncReader, *, max_bytes: int) -> ReceivedUpload:
    """Read, bound, hash and classify an upload.

    Raises a subclass of `UploadRejectedError` if the upload is not acceptable.
    """
    # Returned to the caller, who closes it, so a context manager does not fit here.
    spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MEMORY_LIMIT)  # noqa: SIM115
    digest = hashlib.sha256()
    size = 0

    def consume(chunk: bytes) -> None:
        digest.update(chunk)
        spool.write(chunk)

    try:
        while chunk := await reader.read(CHUNK_SIZE):
            size += len(chunk)
            if size > max_bytes:
                raise UploadTooLargeError
            # Hashing and (possibly disk-backed) writes are off the event loop.
            await asyncio.to_thread(consume, chunk)

        if size == 0:
            raise EmptyUploadError

        document_type = await asyncio.to_thread(detect_document_type, spool)
        if document_type is None:
            raise UnsupportedFileTypeError
        spool.seek(0)
        return ReceivedUpload(
            file=spool, size=size, sha256=digest.hexdigest(), document_type=document_type
        )
    except BaseException:
        spool.close()
        raise

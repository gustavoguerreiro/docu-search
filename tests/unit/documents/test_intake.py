import hashlib
import io
import threading
import zipfile

import pytest

from docusearch.documents.filetypes import DocumentType
from docusearch.documents.intake import (
    CHUNK_SIZE,
    EmptyUploadError,
    UnsupportedFileTypeError,
    UploadRejectedError,
    UploadTooLargeError,
    receive_upload,
)

PDF = b"%PDF-1.4\n" + b"x" * 1000 + b"\n%%EOF\n"


class BytesReader:
    """An async reader over bytes that records how much it served."""

    def __init__(self, data: bytes) -> None:
        self._buffer = io.BytesIO(data)
        self.served = 0
        self.calls = 0

    async def read(self, size: int = -1, /) -> bytes:
        self.calls += 1
        chunk = self._buffer.read(size)
        self.served += len(chunk)
        return chunk


class FailingReader:
    def __init__(self) -> None:
        self._first = True

    async def read(self, size: int = -1, /) -> bytes:
        if self._first:
            self._first = False
            return b"%PDF-1.4 partial"
        raise ConnectionResetError("client went away")


async def test_a_valid_upload_reports_size_hash_and_type() -> None:
    received = await receive_upload(BytesReader(PDF), max_bytes=10_000)
    try:
        assert received.size == len(PDF)
        assert received.sha256 == hashlib.sha256(PDF).hexdigest()
        assert received.document_type is DocumentType.PDF
        assert received.file.read() == PDF  # positioned at the start, ready to be stored
    finally:
        received.close()


async def test_large_uploads_are_hashed_correctly_across_chunks() -> None:
    data = b"%PDF-1.4\n" + bytes(range(256)) * 20_000  # ~5 MB, spills past the memory spool
    reader = BytesReader(data)

    received = await receive_upload(reader, max_bytes=len(data))
    try:
        assert received.size == len(data)
        assert received.sha256 == hashlib.sha256(data).hexdigest()
        assert reader.calls > 2
        assert received.file.read() == data
    finally:
        received.close()


async def test_text_and_docx_uploads_are_classified() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")

    text = await receive_upload(BytesReader("olá, mundo\n".encode()), max_bytes=1000)
    word = await receive_upload(BytesReader(buffer.getvalue()), max_bytes=100_000)
    try:
        assert text.document_type is DocumentType.TXT
        assert word.document_type is DocumentType.DOCX
    finally:
        text.close()
        word.close()


async def test_exactly_the_limit_is_accepted_and_one_byte_more_is_not() -> None:
    at_limit = b"a" * 1000
    received = await receive_upload(BytesReader(at_limit), max_bytes=1000)
    received.close()

    with pytest.raises(UploadTooLargeError):
        await receive_upload(BytesReader(at_limit + b"a"), max_bytes=1000)


async def test_an_oversized_upload_stops_being_read_at_the_limit() -> None:
    huge = b"a" * (50 * CHUNK_SIZE)
    reader = BytesReader(huge)

    with pytest.raises(UploadTooLargeError):
        await receive_upload(reader, max_bytes=2 * CHUNK_SIZE)

    # It may have read one chunk past the limit to notice, never more.
    assert reader.served <= 3 * CHUNK_SIZE


async def test_an_empty_upload_is_rejected() -> None:
    with pytest.raises(EmptyUploadError):
        await receive_upload(BytesReader(b""), max_bytes=1000)


@pytest.mark.parametrize(
    "data",
    [
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 50,
        b"MZ\x90\x00" + b"\x01" * 50,
        "latin-é".encode("latin-1"),
    ],
)
async def test_unsupported_content_is_rejected(data: bytes) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        await receive_upload(BytesReader(data), max_bytes=1000)


async def test_all_rejections_share_a_base_class() -> None:
    for error in (UploadTooLargeError, EmptyUploadError, UnsupportedFileTypeError):
        assert issubclass(error, UploadRejectedError)


async def test_a_dropped_connection_propagates_and_leaves_nothing_open() -> None:
    with pytest.raises(ConnectionResetError):
        await receive_upload(FailingReader(), max_bytes=1000)


async def test_hashing_and_writing_happen_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop_thread = threading.current_thread().name
    seen: list[str] = []
    real_sha256 = hashlib.sha256

    class SpyDigest:
        def __init__(self) -> None:
            self._inner = real_sha256()

        def update(self, chunk: bytes) -> None:
            seen.append(threading.current_thread().name)
            self._inner.update(chunk)

        def hexdigest(self) -> str:
            return self._inner.hexdigest()

    monkeypatch.setattr("docusearch.documents.intake.hashlib.sha256", SpyDigest)

    received = await receive_upload(BytesReader(PDF), max_bytes=10_000)
    received.close()

    assert seen
    assert all(name != loop_thread for name in seen)


async def test_the_hash_identifies_content_not_names_or_types() -> None:
    first = await receive_upload(BytesReader(PDF), max_bytes=10_000)
    second = await receive_upload(BytesReader(PDF), max_bytes=10_000)
    different = await receive_upload(BytesReader(PDF + b"!"), max_bytes=10_000)
    try:
        assert first.sha256 == second.sha256
        assert first.sha256 != different.sha256
    finally:
        for upload in (first, second, different):
            upload.close()

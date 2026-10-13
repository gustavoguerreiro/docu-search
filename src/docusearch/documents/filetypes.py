"""Decide what an uploaded file *is* by looking at its bytes.

The file name and the Content-Type sent by the client are never consulted: both are attacker
controlled. Only three formats are accepted, each with a structural check:

- PDF: begins with `%PDF-`.
- DOCX: a ZIP archive that contains the two parts every Word document has, with limits on entry
  count and total uncompressed size so a small archive cannot promise an enormous expansion later.
- TXT: valid UTF-8 containing no control characters other than tab, newline and carriage return.
  Anything binary, UTF-16 or in another encoding is refused rather than guessed at.

Passing these checks means "plausibly this format", not "safe": extraction still treats the content
as untrusted.
"""

import codecs
import zipfile
from enum import Enum
from typing import IO

_READ_CHUNK = 64 * 1024
_ZIP_MAGIC = b"PK\x03\x04"
_PDF_MAGIC = b"%PDF-"
_DOCX_REQUIRED_PARTS = frozenset({"[Content_Types].xml", "word/document.xml"})

MAX_DOCX_ENTRIES = 10_000
MAX_DOCX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024

# Control characters that do not indicate binary data in a text file.
_ALLOWED_CONTROLS = frozenset("\t\n\r")


class DocumentType(Enum):
    PDF = "application/pdf"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    TXT = "text/plain"

    @property
    def content_type(self) -> str:
        return self.value


def _is_docx(source: IO[bytes], *, max_entries: int, max_uncompressed_bytes: int) -> bool:
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > max_entries:
                return False
            if sum(entry.file_size for entry in entries) > max_uncompressed_bytes:
                return False
            return {entry.filename for entry in entries} >= _DOCX_REQUIRED_PARTS
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        # Not a readable ZIP (or one using something the standard library refuses): not a DOCX.
        return False


def _is_utf8_text(source: IO[bytes]) -> bool:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        while chunk := source.read(_READ_CHUNK):
            text = decoder.decode(chunk)
            if any(ord(ch) < 0x20 and ch not in _ALLOWED_CONTROLS for ch in text):
                return False
            if "\x7f" in text:
                return False
        decoder.decode(b"", final=True)  # a truncated multi-byte sequence at the end is invalid
    except UnicodeDecodeError:
        return False
    return True


def detect_document_type(
    source: IO[bytes],
    *,
    max_docx_entries: int = MAX_DOCX_ENTRIES,
    max_docx_uncompressed_bytes: int = MAX_DOCX_UNCOMPRESSED_BYTES,
) -> DocumentType | None:
    """Return the detected type, or None if the content is not an accepted format.

    `source` must be seekable. The file position is not preserved.
    """
    source.seek(0)
    head = source.read(len(_PDF_MAGIC))
    source.seek(0)
    if head.startswith(_PDF_MAGIC):
        return DocumentType.PDF
    if head[:4] == _ZIP_MAGIC:
        ok = _is_docx(
            source,
            max_entries=max_docx_entries,
            max_uncompressed_bytes=max_docx_uncompressed_bytes,
        )
        return DocumentType.DOCX if ok else None
    source.seek(0)
    return DocumentType.TXT if _is_utf8_text(source) else None

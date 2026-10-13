import io
import os
import zipfile

import pytest

from docusearch.documents.filetypes import DocumentType, detect_document_type

CONTENT_TYPES = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
DOCUMENT = '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>'


def zip_bytes(parts: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def docx() -> bytes:
    return zip_bytes({"[Content_Types].xml": CONTENT_TYPES, "word/document.xml": DOCUMENT})


def detect(data: bytes, **kwargs: int) -> DocumentType | None:
    return detect_document_type(io.BytesIO(data), **kwargs)


# --- accepted ----------------------------------------------------------------------------------


def test_pdf_is_recognized_by_its_header() -> None:
    assert detect(b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n") is DocumentType.PDF


def test_a_real_docx_structure_is_recognized() -> None:
    assert detect(docx()) is DocumentType.DOCX


@pytest.mark.parametrize(
    "text",
    [
        "plain ascii text\n",
        "Acentuação, ñ, ü, 日本語, emoji 🔐\r\nsecond line\ttabbed",
        "﻿with a byte order mark",
        "x",
        "   \n\n   ",
    ],
)
def test_utf8_text_is_recognized(text: str) -> None:
    assert detect(text.encode("utf-8")) is DocumentType.TXT


def test_large_text_spanning_many_read_chunks_is_recognized() -> None:
    # Multi-byte characters will straddle the internal read boundary.
    assert detect(("é" * 200_000).encode("utf-8")) is DocumentType.TXT


def test_declared_content_types_are_the_canonical_ones() -> None:
    assert DocumentType.PDF.content_type == "application/pdf"
    assert DocumentType.TXT.content_type == "text/plain"
    assert DocumentType.DOCX.content_type.endswith("wordprocessingml.document")


# --- refused -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "data"),
    [
        ("png", b"\x89PNG\r\n\x1a\n" + os.urandom(64)),
        ("jpeg", b"\xff\xd8\xff\xe0" + os.urandom(64)),
        ("windows executable", b"MZ\x90\x00" + os.urandom(64)),
        ("elf executable", b"\x7fELF" + os.urandom(64)),
        ("gzip", b"\x1f\x8b\x08" + os.urandom(64)),
        ("random bytes", bytes(range(256)) * 4),
        ("nul bytes", b"text\x00with a nul"),
        ("escape sequences", b"normal\x1b[31mred"),
        ("utf-16 text", "hello world".encode("utf-16")),
        ("latin-1 text", "acentuação".encode("latin-1")),
        ("truncated utf-8", b"abc\xc3"),  # a two-byte sequence cut after its first byte
        ("delete character", b"abc\x7fdef"),
    ],
)
def test_other_content_is_refused(label: str, data: bytes) -> None:
    assert detect(data) is None, label


def test_a_zip_that_is_not_a_word_document_is_refused() -> None:
    plain_zip = zip_bytes({"readme.txt": "hello"})
    xlsx_like = zip_bytes({"[Content_Types].xml": CONTENT_TYPES, "xl/workbook.xml": "<x/>"})
    only_types = zip_bytes({"[Content_Types].xml": CONTENT_TYPES})

    assert detect(plain_zip) is None
    assert detect(xlsx_like) is None
    assert detect(only_types) is None


def test_a_corrupt_or_truncated_zip_is_refused() -> None:
    data = docx()

    assert detect(data[: len(data) // 2]) is None  # central directory is gone
    assert detect(b"PK\x03\x04" + os.urandom(100)) is None


def test_an_archive_with_too_many_entries_is_refused() -> None:
    parts: dict[str, bytes | str] = {
        "[Content_Types].xml": CONTENT_TYPES,
        "word/document.xml": DOCUMENT,
    }
    parts.update({f"extra/{i}.xml": "<x/>" for i in range(50)})

    assert detect(zip_bytes(parts)) is DocumentType.DOCX
    assert detect(zip_bytes(parts), max_docx_entries=20) is None


def test_an_archive_that_expands_beyond_the_limit_is_refused() -> None:
    # Highly compressible: tiny on disk, large once extracted (a miniature zip bomb).
    bomb = zip_bytes(
        {
            "[Content_Types].xml": CONTENT_TYPES,
            "word/document.xml": DOCUMENT,
            "word/media/huge.bin": b"\x00" * (3 * 1024 * 1024),
        }
    )

    assert len(bomb) < 100 * 1024
    assert detect(bomb, max_docx_uncompressed_bytes=1024 * 1024) is None
    assert detect(bomb) is DocumentType.DOCX  # under the real (256 MiB) limit


# --- the declared name and type are not inputs -------------------------------------------------


def test_a_pdf_is_a_pdf_whatever_it_is_called() -> None:
    # detect_document_type only receives bytes: there is nowhere for a file name or MIME type to
    # influence it. This pins that the signature takes no such argument.
    import inspect

    assert list(inspect.signature(detect_document_type).parameters) == [
        "source",
        "max_docx_entries",
        "max_docx_uncompressed_bytes",
    ]


def test_header_wins_over_trailing_content() -> None:
    assert detect(b"%PDF-1.4\n" + docx()) is DocumentType.PDF
    assert (
        detect(b"just text, then PK\x03\x04 bytes".replace(b"PK\x03\x04", b"PK"))
        is DocumentType.TXT
    )

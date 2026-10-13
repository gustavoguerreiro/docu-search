import pytest

from docusearch.documents.filenames import (
    FALLBACK_FILENAME,
    MAX_FILENAME_LENGTH,
    sanitize_filename,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("Relatório Final 2026.docx", "Relatório Final 2026.docx"),
        ("日本語のファイル.txt", "日本語のファイル.txt"),
        ("  padded name.txt  ", "padded name.txt"),
        ("many    spaces\there.txt", "many spaces here.txt"),
    ],
)
def test_ordinary_names_are_kept(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("/etc/shadow", "shadow"),
        ("C:\\Users\\ana\\Documents\\cv.pdf", "cv.pdf"),
        ("C:\\fakepath\\upload.docx", "upload.docx"),
        ("folder/sub/file.txt", "file.txt"),
        ("..\\..\\windows\\system32\\config", "config"),
    ],
)
def test_directory_parts_are_removed(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", ".", "..", "...", "/", "\\", "/..", "\x00", "\n\r\t", '<>:"|?*'],
)
def test_names_with_nothing_usable_fall_back(raw: str) -> None:
    result = sanitize_filename(raw)

    assert result in {FALLBACK_FILENAME, "_______"}
    assert result not in {".", ".."}


def test_none_falls_back() -> None:
    assert sanitize_filename(None) == FALLBACK_FILENAME


def test_control_and_direction_override_characters_are_removed() -> None:
    # U+202E (right-to-left override) is used to make "exe" look like another extension.
    assert sanitize_filename("evil\u202egnp.exe") == "evilgnp.exe"
    assert sanitize_filename("line\nbreak\r.txt") == "linebreak.txt"
    assert sanitize_filename("nul\x00byte.pdf") == "nulbyte.pdf"
    assert sanitize_filename("zero\u200bwidth.txt") == "zerowidth.txt"


def test_characters_reserved_on_common_filesystems_are_replaced() -> None:
    assert sanitize_filename('a<b>c:d"e|f?g*h.txt') == "a_b_c_d_e_f_g_h.txt"


def test_trailing_dots_and_spaces_are_stripped() -> None:
    assert sanitize_filename("report.pdf. . ") == "report.pdf"


def test_unicode_is_normalized() -> None:
    decomposed = "cafe\u0301.txt"  # "e" + combining acute

    assert sanitize_filename(decomposed) == "caf\u00e9.txt"


def test_long_names_are_truncated_but_keep_the_extension() -> None:
    result = sanitize_filename("a" * 1000 + ".pdf")

    assert len(result) == MAX_FILENAME_LENGTH
    assert result.endswith(".pdf")


def test_long_names_without_an_extension_are_cut() -> None:
    assert len(sanitize_filename("b" * 1000)) == MAX_FILENAME_LENGTH


def test_the_result_is_always_a_single_safe_component() -> None:
    nasty = ["../a", "a/../b", "a\\b", "\x00..", "....//....//x", "%2e%2e/x", "a\u202e/b"]

    for raw in nasty:
        result = sanitize_filename(raw)
        assert "/" not in result
        assert "\\" not in result
        assert "\x00" not in result
        assert result not in {".", ".."}
        assert 0 < len(result) <= MAX_FILENAME_LENGTH

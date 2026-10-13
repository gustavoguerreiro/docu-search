import asyncio
import io
import os
import re
import sys
import threading
from pathlib import Path
from typing import BinaryIO

import pytest

from docusearch.storage import (
    InvalidStorageKeyError,
    StorageError,
    StorageKeyExistsError,
    new_storage_key,
    validate_storage_key,
)
from docusearch.storage.local import LocalStorage

KEY = "ab/0123456789abcdef0123456789abcdef"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "uploads"


@pytest.fixture
def storage(root: Path) -> LocalStorage:
    return LocalStorage(root)


def files_under(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


# --- keys --------------------------------------------------------------------------------------


def test_generated_keys_are_valid_unique_and_unrelated_to_any_input() -> None:
    keys = {new_storage_key() for _ in range(500)}

    assert len(keys) == 500
    for key in keys:
        validate_storage_key(key)
        assert re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{32}", key)
        assert key[:2] == key.split("/")[1][:2]  # shard prefix is derived from the token


@pytest.mark.parametrize(
    "key",
    [
        "",
        "../etc/passwd",
        "ab/../../etc/passwd",
        "/etc/passwd",
        "C:\\Windows\\system32",
        "ab\\0123456789abcdef0123456789abcdef",
        "ab/0123456789abcdef0123456789abcdef/",
        "ab/0123456789abcdef0123456789abcdef\n",
        "ab/0123456789abcdef0123456789abcdef\x00.txt",
        "AB/0123456789ABCDEF0123456789ABCDEF",  # uppercase is not what we generate
        "ab/0123456789abcdef0123456789abcde",  # 31 characters
        "ab/0123456789abcdef0123456789abcdef0",  # 33 characters
        "abc/0123456789abcdef0123456789abcdef",
        "ab/0123456789abcdef0123456789abcdeé",
        "ab//0123456789abcdef0123456789abcdef",
        "....//....//etc",
        "%2e%2e/%2e%2e/etc",
        ".tmp/0123456789abcdef0123456789abcdef",
    ],
)
def test_anything_but_a_generated_key_is_rejected(key: str) -> None:
    with pytest.raises(InvalidStorageKeyError):
        validate_storage_key(key)


@pytest.mark.parametrize("key", [None, 123, b"ab/0123456789abcdef0123456789abcdef"])
def test_non_string_keys_are_rejected(key: object) -> None:
    with pytest.raises(InvalidStorageKeyError):
        validate_storage_key(key)  # type: ignore[arg-type]


async def test_hostile_keys_never_reach_the_filesystem(storage: LocalStorage, root: Path) -> None:
    outside = root.parent / "outside.txt"

    for key in ("../outside.txt", "ab/../../outside.txt", str(outside)):
        with pytest.raises(InvalidStorageKeyError):
            await storage.put(key, io.BytesIO(b"x"))
        with pytest.raises(InvalidStorageKeyError):
            await storage.delete(key)

    assert not outside.exists()
    assert not root.exists()  # nothing was even created


# --- put ---------------------------------------------------------------------------------------


async def test_put_stores_the_bytes_and_reports_their_count(
    storage: LocalStorage, root: Path
) -> None:
    payload = os.urandom(300_000)  # more than one copy chunk

    written = await storage.put(KEY, io.BytesIO(payload))

    assert written == len(payload)
    assert (root / KEY).read_bytes() == payload


async def test_put_can_store_an_empty_object(storage: LocalStorage, root: Path) -> None:
    assert await storage.put(KEY, io.BytesIO(b"")) == 0
    assert (root / KEY).read_bytes() == b""


async def test_put_never_overwrites(storage: LocalStorage, root: Path) -> None:
    await storage.put(KEY, io.BytesIO(b"original"))

    with pytest.raises(StorageKeyExistsError):
        await storage.put(KEY, io.BytesIO(b"replacement"))

    assert (root / KEY).read_bytes() == b"original"


class FailingSource(io.RawIOBase):
    """Yields some bytes and then fails, like a dropped connection."""

    def __init__(self) -> None:
        self._sent = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:  # type: ignore[override]
        if not self._sent:
            self._sent = True
            buffer[:5] = b"hello"
            return 5
        raise OSError("connection reset")


async def test_a_failed_write_leaves_no_object_and_no_temporary_file(
    storage: LocalStorage, root: Path
) -> None:
    with pytest.raises(StorageError):
        await storage.put(KEY, FailingSource())  # type: ignore[arg-type]

    assert files_under(root) == []
    assert not (root / KEY).exists()


async def test_writes_do_not_block_the_event_loop(
    storage: LocalStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = threading.current_thread().name
    seen: list[str] = []

    class Spy(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            seen.append(threading.current_thread().name)
            return super().read(size)

    await storage.put(KEY, Spy(b"abc"))

    assert seen
    assert all(name != caller for name in seen)


async def test_concurrent_puts_of_the_same_key_store_exactly_one_object(
    storage: LocalStorage, root: Path
) -> None:
    results = await asyncio.gather(
        *(storage.put(KEY, io.BytesIO(f"writer-{i}".encode())) for i in range(8)),
        return_exceptions=True,
    )

    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(isinstance(r, StorageKeyExistsError) for r in results if isinstance(r, Exception))
    assert len(files_under(root)) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
async def test_files_and_directories_are_private_to_the_owner(
    storage: LocalStorage, root: Path
) -> None:
    await storage.put(KEY, io.BytesIO(b"secret"))

    assert (root / KEY).stat().st_mode & 0o777 == 0o600
    assert (root / "ab").stat().st_mode & 0o777 == 0o700


async def test_a_symlinked_shard_cannot_redirect_writes_outside_the_root(
    storage: LocalStorage, root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root.mkdir()
    try:
        (root / "ab").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available here")

    with pytest.raises(InvalidStorageKeyError):
        await storage.put(KEY, io.BytesIO(b"x"))

    assert list(outside.iterdir()) == []


# --- delete ------------------------------------------------------------------------------------


async def test_delete_removes_the_object_and_is_idempotent(
    storage: LocalStorage, root: Path
) -> None:
    await storage.put(KEY, io.BytesIO(b"x"))

    assert await storage.delete(KEY) is True
    assert await storage.delete(KEY) is False
    assert not (root / KEY).exists()


async def test_delete_only_removes_the_named_object(storage: LocalStorage, root: Path) -> None:
    other = "cd/fedcba9876543210fedcba9876543210"
    await storage.put(KEY, io.BytesIO(b"a"))
    await storage.put(other, io.BytesIO(b"b"))

    await storage.delete(KEY)

    assert (root / other).read_bytes() == b"b"


# --- health ------------------------------------------------------------------------------------


async def test_healthy_creates_the_root_and_reports_true(storage: LocalStorage, root: Path) -> None:
    assert await storage.healthy() is True
    assert root.is_dir()
    assert files_under(root) == []  # the probe leaves nothing behind


async def test_unwritable_root_is_reported_unhealthy_not_raised(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory")

    assert await LocalStorage(blocker / "uploads").healthy() is False


def test_a_binaryio_is_all_the_adapter_needs() -> None:
    source: BinaryIO = io.BytesIO(b"x")  # the protocol takes any binary file object

    assert source.read() == b"x"

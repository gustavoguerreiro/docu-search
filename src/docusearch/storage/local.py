"""Filesystem implementation of `Storage`.

Safety properties:
- Keys must match the generated format, so a path can never be influenced by a client. In addition,
  every directory we write into must resolve to somewhere inside the root, which stops a symlink
  planted in the storage tree from redirecting writes elsewhere (defense in depth).
- Objects are written to a temporary file in the same filesystem and published with a hard link,
  which is atomic and fails instead of overwriting. Readers never see a partial object.
- Blocking file work runs in a worker thread, never on the event loop.
"""

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import IO

from docusearch.storage import (
    InvalidStorageKeyError,
    StorageError,
    StorageKeyExistsError,
    validate_storage_key,
)

logger = logging.getLogger(__name__)

_TMP_DIR = ".tmp"
_COPY_CHUNK = 256 * 1024


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _private_dir(self, path: Path) -> Path:
        """Create `path` if needed and return its real location, which must be inside the root.

        Compares two existing, fully resolved directories. (Resolving a path that does not exist yet
        is not reliable across platforms, which would make this check flaky under concurrency.)
        """
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        real_root = self._root.resolve()
        real = path.resolve()
        if real != real_root and not real.is_relative_to(real_root):
            raise InvalidStorageKeyError("invalid storage key")
        return real

    def _put_sync(self, key: str, source: IO[bytes]) -> int:
        validate_storage_key(key)
        shard, name = key.split("/")
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp_dir = self._private_dir(self._root / _TMP_DIR)
        final = self._private_dir(self._root / shard) / name
        if final.exists():
            raise StorageKeyExistsError("storage key already in use")

        fd, tmp_name = tempfile.mkstemp(dir=tmp_dir)  # created 0600
        try:
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(source, out, _COPY_CHUNK)
                out.flush()
                os.fsync(out.fileno())
            size = Path(tmp_name).stat().st_size
            try:
                os.link(tmp_name, final)  # atomic publish; never replaces an existing object
            except FileExistsError as exc:
                raise StorageKeyExistsError("storage key already in use") from exc
        finally:
            Path(tmp_name).unlink(missing_ok=True)
        return size

    async def put(self, key: str, source: IO[bytes]) -> int:
        try:
            return await asyncio.to_thread(self._put_sync, key, source)
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError("could not store object") from exc

    def _delete_sync(self, key: str) -> bool:
        validate_storage_key(key)
        shard, name = key.split("/")
        shard_dir = self._root / shard
        if not shard_dir.is_dir():
            return False
        path = self._private_dir(shard_dir) / name
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    async def delete(self, key: str) -> bool:
        try:
            return await asyncio.to_thread(self._delete_sync, key)
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError("could not delete object") from exc

    def _healthy_sync(self) -> bool:
        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=self._root):
                pass
        except OSError:
            return False
        return True

    async def healthy(self) -> bool:
        healthy = await asyncio.to_thread(self._healthy_sync)
        if not healthy:
            logger.warning("storage is not writable")
        return healthy

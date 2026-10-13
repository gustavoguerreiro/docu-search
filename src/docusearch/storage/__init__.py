"""Where uploaded files live.

Domain code talks to the `Storage` protocol and never to the filesystem, so the local adapter used
today can be replaced by S3 without touching the callers. Keys are generated here, on the server;
nothing a client sends is ever part of a key.
"""

import re
import secrets
from typing import BinaryIO, Protocol

# "ab/0123456789abcdef0123456789abcdef": a two-character shard directory and 128 random bits.
_KEY_PATTERN = re.compile(r"[0-9a-f]{2}/[0-9a-f]{32}")


class StorageError(Exception):
    """A storage operation failed."""


class InvalidStorageKeyError(StorageError):
    """The key is not one this system generates. Never derived from user input."""


class StorageKeyExistsError(StorageError):
    """An object already exists under this key; storage never overwrites."""


def new_storage_key() -> str:
    token = secrets.token_hex(16)
    return f"{token[:2]}/{token}"


def validate_storage_key(key: str) -> None:
    # fullmatch, so a trailing newline or any extra character cannot slip through.
    if not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key):
        raise InvalidStorageKeyError("invalid storage key")


class Storage(Protocol):
    async def put(self, key: str, source: BinaryIO) -> int:
        """Store everything readable from `source` under a new key; returns the byte count.

        All or nothing: after a failure there is no object under `key`. Raises
        `StorageKeyExistsError` rather than replacing an existing object.
        """
        ...

    async def delete(self, key: str) -> bool:
        """Remove an object. True if it existed; deleting a missing object is not an error."""
        ...

    async def healthy(self) -> bool:
        """True if the backend can currently be written to (used by the readiness probe)."""
        ...

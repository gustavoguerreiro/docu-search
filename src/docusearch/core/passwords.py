"""Password hashing with Argon2id.

Parameters are argon2-cffi's defaults (Argon2id, 3 iterations, 64 MiB, 4 lanes: the RFC 9106
low-memory profile). Hashing is CPU- and memory-heavy, so it never runs on the event loop and runs
on a small dedicated pool: concurrent logins cannot use more than
`_MAX_CONCURRENT_HASHES * 64 MiB`. Rate limiting of the endpoints that call this arrives with the
API hardening milestone.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# Bytes, not characters. Far above any sensible password; it only bounds the work an attacker can
# make us do with one request.
MAX_PASSWORD_BYTES = 1024
_MAX_CONCURRENT_HASHES = 4

_hasher = PasswordHasher()
_executor = ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_HASHES, thread_name_prefix="argon2")


def _too_long(password: str) -> bool:
    return len(password.encode("utf-8")) > MAX_PASSWORD_BYTES


async def hash_password(password: str) -> str:
    """Return an encoded Argon2id hash (salt and parameters included). Raises ValueError if the
    password exceeds `MAX_PASSWORD_BYTES`."""
    if _too_long(password):
        raise ValueError("password is too long")
    return await asyncio.get_running_loop().run_in_executor(_executor, _hasher.hash, password)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        # Wrong password, or a stored value that is not an Argon2 hash: both mean "no".
        return False


async def verify_password(password: str, password_hash: str) -> bool:
    """True only if `password` matches. Never raises for bad input: an oversized password or a
    malformed hash simply does not verify."""
    if _too_long(password):
        return False
    return await asyncio.get_running_loop().run_in_executor(
        _executor, _verify, password_hash, password
    )


def needs_rehash(password_hash: str) -> bool:
    """True if the hash used weaker parameters than the current ones (cheap; no hashing)."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True

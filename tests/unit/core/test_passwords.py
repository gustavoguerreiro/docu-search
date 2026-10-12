import asyncio
import threading

import pytest
from argon2 import PasswordHasher

from docusearch.core.passwords import (
    MAX_PASSWORD_BYTES,
    hash_password,
    needs_rehash,
    verify_password,
)

PASSWORD = "correct horse battery staple"


async def test_hash_is_argon2id_and_never_contains_the_password() -> None:
    hashed = await hash_password(PASSWORD)

    assert hashed.startswith("$argon2id$")
    assert PASSWORD not in hashed


async def test_hashes_are_salted() -> None:
    first, second = await asyncio.gather(hash_password(PASSWORD), hash_password(PASSWORD))

    assert first != second


async def test_correct_password_verifies_and_wrong_ones_do_not() -> None:
    hashed = await hash_password(PASSWORD)

    assert await verify_password(PASSWORD, hashed) is True
    assert await verify_password(PASSWORD + "x", hashed) is False
    assert await verify_password("", hashed) is False
    assert await verify_password(PASSWORD.upper(), hashed) is False


async def test_unicode_passwords_round_trip() -> None:
    password = "pässwörd-密碼-🔐"
    hashed = await hash_password(password)

    assert await verify_password(password, hashed) is True
    assert await verify_password("passwörd-密碼-🔐", hashed) is False


@pytest.mark.parametrize("stored", ["", "not-a-hash", "$argon2id$garbage", "plaintext-password"])
async def test_malformed_stored_hashes_do_not_verify_and_do_not_raise(stored: str) -> None:
    assert await verify_password(PASSWORD, stored) is False


async def test_oversized_passwords_are_refused_before_any_hashing() -> None:
    huge = "a" * (MAX_PASSWORD_BYTES + 1)
    hashed = await hash_password(PASSWORD)

    with pytest.raises(ValueError, match="too long"):
        await hash_password(huge)
    assert await verify_password(huge, hashed) is False


async def test_the_limit_is_in_bytes_not_characters() -> None:
    # 400 four-byte characters are 400 characters but 1600 bytes.
    with pytest.raises(ValueError, match="too long"):
        await hash_password("🔐" * 400)
    assert len(await hash_password("a" * MAX_PASSWORD_BYTES)) > 0


async def test_needs_rehash_flags_weaker_parameters_only() -> None:
    current = await hash_password(PASSWORD)
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)

    assert needs_rehash(current) is False
    assert needs_rehash(weak) is True
    assert needs_rehash("not-a-hash") is True


async def test_hashing_and_verifying_run_on_the_dedicated_pool_not_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop_thread = threading.current_thread().name
    seen: list[str] = []

    class SpyHasher:
        def hash(self, password: str) -> str:
            seen.append(threading.current_thread().name)
            return "$argon2id$fake"

        def verify(self, password_hash: str, password: str) -> bool:
            seen.append(threading.current_thread().name)
            return True

    monkeypatch.setattr("docusearch.core.passwords._hasher", SpyHasher())

    await hash_password(PASSWORD)
    await verify_password(PASSWORD, "$argon2id$fake")

    assert len(seen) == 2
    assert all(name != loop_thread for name in seen)
    assert all(name.startswith("argon2") for name in seen)

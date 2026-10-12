from typing import Any, cast

import pytest
from bson import ObjectId
from pydantic import SecretStr

from docusearch.auth.service import AuthService, InvalidCredentialsError
from docusearch.core.clock import utc_now
from docusearch.core.passwords import hash_password as hash_password_real
from docusearch.core.passwords import verify_password as verify_password_real
from docusearch.core.settings import Settings
from docusearch.db.models import User
from docusearch.db.repositories.users import EmailAlreadyExistsError, normalize_email


class FakeUsers:
    def __init__(self) -> None:
        self.by_email: dict[str, User] = {}
        self.create_calls = 0

    async def create(self, *, email: str, password_hash: str) -> User:
        self.create_calls += 1
        email = normalize_email(email)
        if email in self.by_email:
            raise EmailAlreadyExistsError
        now = utc_now()
        user = User(
            _id=ObjectId(), email=email, password_hash=password_hash, created_at=now, updated_at=now
        )  # type: ignore[call-arg]
        self.by_email[email] = user
        return user

    async def get_by_email(self, email: str) -> User | None:
        return self.by_email.get(normalize_email(email))


class FakeRefresh:
    def __init__(self) -> None:
        self.issued: list[ObjectId] = []

    async def issue(self, user_id: ObjectId) -> Any:
        self.issued.append(user_id)
        return type("Issued", (), {"token": "refresh-token-value", "expires_at": utc_now()})()


@pytest.fixture
def users() -> FakeUsers:
    return FakeUsers()


@pytest.fixture
def refresh() -> FakeRefresh:
    return FakeRefresh()


@pytest.fixture
def service(users: FakeUsers, refresh: FakeRefresh) -> AuthService:
    settings = Settings(_env_file=None, jwt_secret_key=SecretStr("k" * 40))
    return AuthService(cast(Any, users), cast(Any, refresh), settings)


class HashSpy:
    """Counts password-hashing work so tests can assert it happens on every path."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.hashes = 0
        self.verifies: list[str] = []
        real_hash, real_verify = hash_password_real, verify_password_real

        async def hash_password(password: str) -> str:
            self.hashes += 1
            return await real_hash(password)

        async def verify_password(password: str, password_hash: str) -> bool:
            self.verifies.append(password_hash)
            return await real_verify(password, password_hash)

        monkeypatch.setattr("docusearch.auth.service.hash_password", hash_password)
        monkeypatch.setattr("docusearch.auth.service.verify_password", verify_password)


async def test_register_hashes_even_when_the_email_is_already_taken(
    service: AuthService, users: FakeUsers, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = HashSpy(monkeypatch)
    await service.register("ana@example.com", "a-long-enough-password")

    await service.register("ANA@example.com", "another-long-password")  # no error, no signal

    assert spy.hashes == 2  # the expensive step ran for the duplicate too
    assert users.create_calls == 2
    assert len(users.by_email) == 1


async def test_register_stores_a_hash_not_the_password(
    service: AuthService, users: FakeUsers
) -> None:
    await service.register("ana@example.com", "a-long-enough-password")

    stored = users.by_email["ana@example.com"].password_hash
    assert stored.startswith("$argon2id$")
    assert "a-long-enough-password" not in stored


async def test_login_succeeds_and_returns_both_tokens(
    service: AuthService, users: FakeUsers, refresh: FakeRefresh
) -> None:
    await service.register("ana@example.com", "a-long-enough-password")

    pair = await service.login(" Ana@Example.com ", "a-long-enough-password")

    assert pair.access_token.count(".") == 2  # a JWT
    assert pair.refresh_token == "refresh-token-value"
    assert pair.expires_in == 15 * 60
    assert refresh.issued == [users.by_email["ana@example.com"].id]
    assert "refresh-token-value" not in repr(pair)


async def test_unknown_email_and_wrong_password_are_the_same_error_and_cost_the_same_work(
    service: AuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    await service.register("ana@example.com", "a-long-enough-password")
    spy = HashSpy(monkeypatch)

    with pytest.raises(InvalidCredentialsError) as wrong_password:
        await service.login("ana@example.com", "not-the-password")
    with pytest.raises(InvalidCredentialsError) as unknown_email:
        await service.login("nobody@example.com", "whatever-password")

    assert type(wrong_password.value) is type(unknown_email.value)
    assert len(spy.verifies) == 2  # a verification happened for the unknown email as well
    assert all(h.startswith("$argon2id$") for h in spy.verifies)


async def test_dummy_hash_is_created_once_and_never_matches_a_real_password(
    service: AuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = HashSpy(monkeypatch)

    for _ in range(3):
        with pytest.raises(InvalidCredentialsError):
            await service.login("nobody@example.com", "a-long-enough-password")

    assert spy.hashes == 1  # the dummy hash, computed on first use only
    assert len(set(spy.verifies)) == 1

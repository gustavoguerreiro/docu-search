import re

import pytest

from docusearch.auth.refresh_tokens import (
    InvalidRefreshTokenError,
    IssuedRefreshToken,
    RefreshTokenService,
    generate_token,
    hash_token,
)
from docusearch.core.clock import utc_now
from docusearch.core.settings import Settings


def test_tokens_are_long_unique_and_urlsafe() -> None:
    tokens = {generate_token() for _ in range(200)}

    assert len(tokens) == 200
    for token in tokens:
        assert len(token) >= 43  # 32 random bytes, base64url without padding
        assert re.fullmatch(r"[A-Za-z0-9_-]+", token)


def test_hash_is_deterministic_hex_and_not_the_token() -> None:
    token = generate_token()

    assert hash_token(token) == hash_token(token)
    assert re.fullmatch(r"[0-9a-f]{64}", hash_token(token))
    assert token not in hash_token(token)
    assert hash_token(token) != hash_token(token + "x")


def test_issued_token_is_hidden_from_repr() -> None:
    issued = IssuedRefreshToken(token="super-secret-refresh-token", expires_at=utc_now())

    assert "super-secret" not in repr(issued)
    assert "super-secret" not in str(issued)


class ExplodingRepository:
    """Any database access fails the test: malformed input must be rejected before the database."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"unexpected repository access: {name}")


@pytest.fixture
def service() -> RefreshTokenService:
    return RefreshTokenService(ExplodingRepository(), Settings(_env_file=None))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "short",
        "a" * 15,
        "a" * 129,
        "has space " + "a" * 20,
        "ünïcode" * 5,
        "bad/chars+" * 4,
        "\n" * 20,
    ],
)
async def test_malformed_tokens_are_rejected_without_touching_the_database(
    service: RefreshTokenService, garbage: str
) -> None:
    with pytest.raises(InvalidRefreshTokenError):
        await service.rotate(garbage)


async def test_logout_with_a_malformed_token_is_a_silent_no_op(
    service: RefreshTokenService,
) -> None:
    await service.revoke("not a token")  # must neither raise nor query


def test_the_error_carries_no_detail() -> None:
    assert str(InvalidRefreshTokenError()) == ""

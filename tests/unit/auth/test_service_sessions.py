from typing import Any, cast

import pytest
from bson import ObjectId
from pydantic import SecretStr

from docusearch.auth.refresh_tokens import (
    InvalidRefreshTokenError,
    IssuedRefreshToken,
    RotatedSession,
)
from docusearch.auth.service import AuthService
from docusearch.core.clock import utc_now
from docusearch.core.settings import Settings
from docusearch.core.tokens import decode_access_token

USER_ID = ObjectId()


class FakeRefresh:
    def __init__(self) -> None:
        self.rotated: list[str] = []
        self.revoked: list[str] = []
        self.revoked_users: list[ObjectId] = []
        self.fail = False

    async def rotate(self, token: str) -> RotatedSession:
        self.rotated.append(token)
        if self.fail:
            raise InvalidRefreshTokenError
        return RotatedSession(
            user_id=USER_ID,
            refresh_token=IssuedRefreshToken(token="next-token", expires_at=utc_now()),
        )

    async def revoke(self, token: str) -> None:
        self.revoked.append(token)

    async def revoke_all_for_user(self, user_id: ObjectId) -> int:
        self.revoked_users.append(user_id)
        return 1


class FakeUsers:
    def __init__(self, exists: bool = True) -> None:
        self.exists = exists

    async def get_by_id(self, user_id: ObjectId) -> object | None:
        return object() if self.exists else None


def make(users: FakeUsers, refresh: FakeRefresh) -> tuple[AuthService, Settings]:
    settings = Settings(_env_file=None, jwt_secret_key=SecretStr("k" * 40))
    return AuthService(cast(Any, users), cast(Any, refresh), settings), settings


async def test_refresh_returns_a_fresh_pair_for_the_users_id() -> None:
    refresh = FakeRefresh()
    service, settings = make(FakeUsers(), refresh)

    pair = await service.refresh("old-token")

    assert refresh.rotated == ["old-token"]
    assert pair.refresh_token == "next-token"
    assert decode_access_token(pair.access_token, settings).user_id == USER_ID
    assert pair.expires_in == settings.access_token_ttl_minutes * 60


async def test_refresh_propagates_an_invalid_token() -> None:
    refresh = FakeRefresh()
    refresh.fail = True
    service, _ = make(FakeUsers(), refresh)

    with pytest.raises(InvalidRefreshTokenError):
        await service.refresh("whatever")

    assert refresh.revoked_users == []


async def test_refresh_for_a_missing_user_ends_all_their_sessions_and_fails() -> None:
    refresh = FakeRefresh()
    service, _ = make(FakeUsers(exists=False), refresh)

    with pytest.raises(InvalidRefreshTokenError):
        await service.refresh("old-token")

    assert refresh.revoked_users == [USER_ID]


async def test_logout_delegates_and_never_raises() -> None:
    refresh = FakeRefresh()
    service, _ = make(FakeUsers(), refresh)

    await service.logout("some-token")

    assert refresh.revoked == ["some-token"]

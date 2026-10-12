from datetime import timedelta
from typing import Any, cast

import pytest
from bson import ObjectId
from pydantic import SecretStr

from docusearch.auth.service import AuthService
from docusearch.core.clock import utc_now
from docusearch.core.settings import Settings
from docusearch.core.tokens import ExpiredTokenError, InvalidTokenError, create_access_token
from docusearch.db.models import User

USER_ID = ObjectId()


class FakeUsers:
    def __init__(self, exists: bool = True) -> None:
        self.exists = exists
        self.lookups: list[ObjectId] = []

    async def get_by_id(self, user_id: ObjectId) -> User | None:
        self.lookups.append(user_id)
        if not self.exists:
            return None
        now = utc_now()
        return User.model_validate(
            {
                "_id": user_id,
                "email": "ana@example.com",
                "password_hash": "x",
                "created_at": now,
                "updated_at": now,
            }
        )


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, jwt_secret_key=SecretStr("k" * 40))


def make(users: FakeUsers, settings: Settings) -> AuthService:
    return AuthService(cast(Any, users), cast(Any, object()), settings)


async def test_a_valid_token_resolves_to_its_user(settings: Settings) -> None:
    users = FakeUsers()

    user = await make(users, settings).authenticate(create_access_token(USER_ID, settings))

    assert user.id == USER_ID
    assert users.lookups == [USER_ID]


async def test_a_token_for_a_missing_user_is_invalid(settings: Settings) -> None:
    token = create_access_token(USER_ID, settings)

    with pytest.raises(InvalidTokenError):
        await make(FakeUsers(exists=False), settings).authenticate(token)


async def test_an_expired_token_is_reported_as_expired_without_a_database_lookup(
    settings: Settings,
) -> None:
    users = FakeUsers()
    token = create_access_token(USER_ID, settings, now=utc_now() - timedelta(days=1))

    with pytest.raises(ExpiredTokenError):
        await make(users, settings).authenticate(token)

    assert users.lookups == []


async def test_a_forged_token_never_reaches_the_database(settings: Settings) -> None:
    users = FakeUsers()
    other = Settings(_env_file=None, jwt_secret_key=SecretStr("z" * 40))

    with pytest.raises(InvalidTokenError):
        await make(users, settings).authenticate(create_access_token(USER_ID, other))

    assert users.lookups == []

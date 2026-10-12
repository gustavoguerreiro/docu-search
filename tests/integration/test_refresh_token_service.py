import asyncio
import logging
from datetime import timedelta

import pytest
from bson import ObjectId
from pydantic import SecretStr

from docusearch.auth.refresh_tokens import (
    InvalidRefreshTokenError,
    RefreshTokenService,
    generate_token,
    hash_token,
)
from docusearch.core.clock import utc_now
from docusearch.core.settings import Settings
from docusearch.db import collections
from docusearch.db.indexes import ensure_indexes
from docusearch.db.mongo import MongoDatabase
from docusearch.db.repositories.refresh_tokens import RefreshTokenRepository

TTL_DAYS = 7


@pytest.fixture
async def db(mongo_db: MongoDatabase) -> MongoDatabase:
    await ensure_indexes(mongo_db)
    return mongo_db


@pytest.fixture
def repository(db: MongoDatabase) -> RefreshTokenRepository:
    return RefreshTokenRepository(db)


@pytest.fixture
def service(repository: RefreshTokenRepository) -> RefreshTokenService:
    settings = Settings(
        _env_file=None, jwt_secret_key=SecretStr("k" * 40), refresh_token_ttl_days=TTL_DAYS
    )
    return RefreshTokenService(repository, settings)


async def live_tokens(db: MongoDatabase, user_id: ObjectId) -> int:
    return await db[collections.REFRESH_TOKENS].count_documents(
        {"user_id": user_id, "revoked_at": None}
    )


async def test_issue_stores_only_the_hash(
    service: RefreshTokenService, db: MongoDatabase, repository: RefreshTokenRepository
) -> None:
    user = ObjectId()

    issued = await service.issue(user)

    stored = await db[collections.REFRESH_TOKENS].find_one({"user_id": user})
    assert stored is not None
    assert stored["token_hash"] == hash_token(issued.token)
    assert issued.token not in {str(v) for v in stored.values()}
    assert stored["revoked_at"] is None
    assert timedelta(days=TTL_DAYS) - timedelta(minutes=1) < issued.expires_at - utc_now()
    assert issued.expires_at - utc_now() <= timedelta(days=TTL_DAYS)


async def test_rotation_returns_a_new_token_and_retires_the_old_one(
    service: RefreshTokenService, db: MongoDatabase
) -> None:
    user = ObjectId()
    first = await service.issue(user)

    rotated = await service.rotate(first.token)

    assert rotated.user_id == user
    assert rotated.refresh_token.token != first.token
    assert await live_tokens(db, user) == 1  # only the successor is live
    # the old token is now unusable, the new one works
    second = await service.rotate(rotated.refresh_token.token)
    assert second.user_id == user


async def test_rotation_never_extends_the_session(service: RefreshTokenService) -> None:
    first = await service.issue(ObjectId())

    second = await service.rotate(first.token)
    third = await service.rotate(second.refresh_token.token)

    assert second.refresh_token.expires_at == first.expires_at
    assert third.refresh_token.expires_at == first.expires_at


async def test_reusing_a_rotated_token_revokes_the_whole_session(
    service: RefreshTokenService, db: MongoDatabase, caplog: pytest.LogCaptureFixture
) -> None:
    user = ObjectId()
    stolen = await service.issue(user)
    legit_next = await service.rotate(stolen.token)

    with caplog.at_level(logging.WARNING), pytest.raises(InvalidRefreshTokenError):
        await service.rotate(stolen.token)  # the attacker (or a replay) presents the old token

    assert await live_tokens(db, user) == 0
    with pytest.raises(InvalidRefreshTokenError):
        await service.rotate(legit_next.refresh_token.token)  # the legitimate chain is dead too
    record = next(r for r in caplog.records if "reuse" in r.getMessage())
    assert record.__dict__["user_id"] == str(user)
    assert stolen.token not in caplog.text


async def test_reuse_in_one_session_does_not_affect_another_session_of_the_same_user(
    service: RefreshTokenService, db: MongoDatabase
) -> None:
    user = ObjectId()
    laptop = await service.issue(user)
    phone = await service.issue(user)
    await service.rotate(laptop.token)

    with pytest.raises(InvalidRefreshTokenError):
        await service.rotate(laptop.token)  # reuse on the laptop session

    assert (await service.rotate(phone.token)).user_id == user  # the phone is unaffected


async def test_concurrent_rotations_of_one_token_yield_at_most_one_success_and_no_live_token(
    service: RefreshTokenService, db: MongoDatabase
) -> None:
    user = ObjectId()
    issued = await service.issue(user)

    results = await asyncio.gather(
        *(service.rotate(issued.token) for _ in range(8)), return_exceptions=True
    )

    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, InvalidRefreshTokenError)]
    assert len(successes) <= 1
    assert len(successes) + len(failures) == 8  # nothing else went wrong
    # The race was detected as reuse, so the session must not have a live token left over.
    assert await live_tokens(db, user) == 0


async def test_expired_token_is_rejected(
    service: RefreshTokenService, repository: RefreshTokenRepository, db: MongoDatabase
) -> None:
    user = ObjectId()
    token = generate_token()
    await repository.create(
        user_id=user,
        family_id=ObjectId(),
        token_hash=hash_token(token),
        expires_at=utc_now() - timedelta(seconds=1),
    )

    with pytest.raises(InvalidRefreshTokenError):
        await service.rotate(token)

    assert (
        await db[collections.REFRESH_TOKENS].count_documents({"user_id": user}) == 1
    )  # no successor


async def test_unknown_token_is_rejected(service: RefreshTokenService) -> None:
    with pytest.raises(InvalidRefreshTokenError):
        await service.rotate(generate_token())


async def test_logout_revokes_the_session_and_is_idempotent(
    service: RefreshTokenService, db: MongoDatabase
) -> None:
    user = ObjectId()
    session = await service.issue(user)
    current = (await service.rotate(session.token)).refresh_token.token

    await service.revoke(current)
    await service.revoke(current)  # again: no error
    await service.revoke(generate_token())  # unknown token: no error, no information

    assert await live_tokens(db, user) == 0
    with pytest.raises(InvalidRefreshTokenError):
        await service.rotate(current)


async def test_revoke_all_ends_every_session_of_the_user_only(
    service: RefreshTokenService, db: MongoDatabase
) -> None:
    ana, bia = ObjectId(), ObjectId()
    await service.issue(ana)
    await service.issue(ana)
    other = await service.issue(bia)

    assert await service.revoke_all_for_user(ana) == 2

    assert await live_tokens(db, ana) == 0
    assert (await service.rotate(other.token)).user_id == bia

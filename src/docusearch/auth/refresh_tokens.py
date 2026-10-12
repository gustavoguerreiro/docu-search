"""Refresh tokens: opaque, single-use, rotated on every refresh.

A *family* is the chain of tokens descending from one login. Each refresh revokes the presented
token and issues the next one in the same family. A token that is presented after it was already
revoked means two parties hold it (or a client replayed it), so the whole family is revoked: the
legitimate user has to log in again, and so does whoever stole the token.

Only SHA-256 hashes are stored. The tokens carry 256 bits of randomness, so a fast hash is
appropriate here (unlike passwords).
"""

import hashlib
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from bson import ObjectId

from docusearch.core.clock import utc_now
from docusearch.core.settings import Settings
from docusearch.db.repositories.refresh_tokens import RefreshTokenRepository

logger = logging.getLogger(__name__)

_TOKEN_BYTES = 32
# Shape of what `secrets.token_urlsafe(32)` produces, with headroom. Anything else is rejected
# before touching the database or the hash function.
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,128}")


class InvalidRefreshTokenError(Exception):
    """The refresh token is unknown, expired, revoked or malformed. Carries no detail on purpose."""


@dataclass(frozen=True)
class IssuedRefreshToken:
    token: str = field(repr=False)  # the only time the raw value exists server-side
    expires_at: datetime


@dataclass(frozen=True)
class RotatedSession:
    user_id: ObjectId
    refresh_token: IssuedRefreshToken


def generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class RefreshTokenService:
    def __init__(self, repository: RefreshTokenRepository, settings: Settings) -> None:
        self._repository = repository
        self._ttl = timedelta(days=settings.refresh_token_ttl_days)

    async def issue(self, user_id: ObjectId) -> IssuedRefreshToken:
        """Start a new session (family) for a user who has just authenticated."""
        token = generate_token()
        expires_at = utc_now() + self._ttl
        await self._repository.create(
            user_id=user_id,
            family_id=ObjectId(),
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
        return IssuedRefreshToken(token=token, expires_at=expires_at)

    async def rotate(self, token: str) -> RotatedSession:
        """Exchange a valid token for the next one in its family.

        The new token inherits the old expiry, so a session ends a fixed time after login no matter
        how often it is refreshed. Raises `InvalidRefreshTokenError` for every failure.
        """
        if not _TOKEN_PATTERN.fullmatch(token):
            raise InvalidRefreshTokenError
        old_hash = hash_token(token)

        record = await self._repository.get_by_hash(old_hash)
        if record is None:
            raise InvalidRefreshTokenError
        if record.revoked_at is not None:
            await self._handle_reuse(record.family_id, record.user_id)
            raise InvalidRefreshTokenError
        if record.expires_at <= utc_now():
            raise InvalidRefreshTokenError

        # Create the successor first, then revoke the old token atomically. If another request
        # revoked it in between, the family is revoked (successor included), so a reuse can never
        # leave a live token behind. A crash between the two steps leaves an undelivered, unknown
        # token that simply expires.
        new_token = generate_token()
        await self._repository.create(
            user_id=record.user_id,
            family_id=record.family_id,
            token_hash=hash_token(new_token),
            expires_at=record.expires_at,
        )
        if not await self._repository.revoke(old_hash):
            await self._handle_reuse(record.family_id, record.user_id)
            raise InvalidRefreshTokenError

        return RotatedSession(
            user_id=record.user_id,
            refresh_token=IssuedRefreshToken(token=new_token, expires_at=record.expires_at),
        )

    async def revoke(self, token: str) -> None:
        """Log out: end the session this token belongs to. Idempotent and silent for unknown or
        malformed tokens, so logout reveals nothing about which tokens exist."""
        if not _TOKEN_PATTERN.fullmatch(token):
            return
        record = await self._repository.get_by_hash(hash_token(token))
        if record is not None:
            await self._repository.revoke_family(record.family_id)

    async def revoke_all_for_user(self, user_id: ObjectId) -> int:
        """End every session of a user (e.g. after a password change)."""
        return await self._repository.revoke_all_for_user(user_id)

    async def _handle_reuse(self, family_id: ObjectId, user_id: ObjectId) -> None:
        await self._repository.revoke_family(family_id)
        logger.warning(
            "refresh token reuse detected; session revoked",
            extra={"user_id": str(user_id), "family_id": str(family_id)},
        )

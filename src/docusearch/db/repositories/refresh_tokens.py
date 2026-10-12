from datetime import datetime

from bson import ObjectId

from docusearch.core.clock import utc_now
from docusearch.db import collections
from docusearch.db.models import RefreshToken
from docusearch.db.mongo import MongoDatabase


class RefreshTokenRepository:
    """Stores only token hashes. Expiry checks and rotation policy belong to the auth service."""

    def __init__(self, db: MongoDatabase) -> None:
        self._tokens = db[collections.REFRESH_TOKENS]

    async def create(
        self, *, user_id: ObjectId, token_hash: str, expires_at: datetime
    ) -> RefreshToken:
        if expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        document = {
            "user_id": user_id,
            "token_hash": token_hash,
            "expires_at": expires_at,
            "created_at": utc_now(),
            "revoked_at": None,
        }
        result = await self._tokens.insert_one(document)
        return RefreshToken.model_validate({**document, "_id": result.inserted_id})

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        found = await self._tokens.find_one({"token_hash": token_hash})
        return RefreshToken.model_validate(found) if found else None

    async def revoke(self, token_hash: str) -> bool:
        """Revoke atomically. True only for the call that performed the revocation, so two
        concurrent refreshes with the same token cannot both succeed."""
        result = await self._tokens.update_one(
            {"token_hash": token_hash, "revoked_at": None},
            {"$set": {"revoked_at": utc_now()}},
        )
        return result.modified_count == 1

    async def revoke_all_for_user(self, user_id: ObjectId) -> int:
        result = await self._tokens.update_many(
            {"user_id": user_id, "revoked_at": None},
            {"$set": {"revoked_at": utc_now()}},
        )
        return result.modified_count

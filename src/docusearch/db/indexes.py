"""Index definitions, creation and verification.

Every index below exists for a named access pattern (see docs/decisions.md, ADR-0013). Index names
are explicit so creation is idempotent and verification can match them. Text indexes for search are
added with the search milestone.
"""

import logging

from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.errors import PyMongoError

from docusearch.db import collections
from docusearch.db.mongo import MongoDatabase

logger = logging.getLogger(__name__)

INDEXES: dict[str, list[IndexModel]] = {
    collections.USERS: [
        # Login lookup and atomic "email already registered". Emails are stored normalized.
        IndexModel([("email", ASCENDING)], name="email_unique", unique=True),
    ],
    collections.DOCUMENTS: [
        # List a user's documents, newest first.
        IndexModel([("owner_id", ASCENDING), ("created_at", DESCENDING)], name="owner_created"),
        # Per-user duplicate detection that is safe under concurrent uploads.
        IndexModel(
            [("owner_id", ASCENDING), ("sha256", ASCENDING)],
            name="owner_sha256_unique",
            unique=True,
        ),
        # Find documents stuck in pending/processing (reconciliation).
        IndexModel([("status", ASCENDING), ("updated_at", ASCENDING)], name="status_updated"),
    ],
    collections.CHUNKS: [
        # Chunks of a document in order; deleting by document; idempotent reprocessing, because a
        # repeated job cannot insert the same (document, index) twice.
        IndexModel(
            [("document_id", ASCENDING), ("chunk_index", ASCENDING)],
            name="document_chunk_unique",
            unique=True,
        ),
    ],
    collections.REFRESH_TOKENS: [
        # Lookup on refresh; only the token hash is stored.
        IndexModel([("token_hash", ASCENDING)], name="token_hash_unique", unique=True),
        # MongoDB removes expired sessions on its own.
        IndexModel([("expires_at", ASCENDING)], name="expires_ttl", expireAfterSeconds=0),
        # Revoke all sessions of a user.
        IndexModel([("user_id", ASCENDING)], name="user_id"),
    ],
}


async def ensure_indexes(db: MongoDatabase) -> None:
    """Create every required index. Idempotent.

    Re-creating an identical index is a no-op. An index with the same name but different options
    raises `OperationFailure` instead of being silently replaced: that is a schema decision that
    needs a migration, not a startup side effect.
    """
    for collection_name, models in INDEXES.items():
        await db[collection_name].create_indexes(models)


async def verify_indexes(db: MongoDatabase) -> list[str]:
    """Return a description of every required index that is missing or differs. Empty means OK."""
    problems: list[str] = []
    for collection_name, models in INDEXES.items():
        existing = await db[collection_name].index_information()
        for model in models:
            expected = model.document
            name = expected["name"]
            actual = existing.get(name)
            if actual is None:
                problems.append(f"{collection_name}.{name}: missing")
                continue
            if list(expected["key"].items()) != [tuple(pair) for pair in actual["key"]]:
                problems.append(f"{collection_name}.{name}: different key")
            if bool(expected.get("unique", False)) != bool(actual.get("unique", False)):
                problems.append(f"{collection_name}.{name}: different uniqueness")
            if expected.get("expireAfterSeconds") != actual.get("expireAfterSeconds"):
                problems.append(f"{collection_name}.{name}: different TTL")
    return problems


class IndexInitializer:
    """Creates indexes once, tolerating a database that is down at startup.

    `ensure` doubles as the `indexes` readiness check: until it has succeeded it retries on every
    call, so a process that started while MongoDB was unavailable repairs itself without a restart.
    """

    def __init__(self, db: MongoDatabase) -> None:
        self._db = db
        self._done = False

    @property
    def done(self) -> bool:
        return self._done

    async def ensure(self) -> bool:
        if self._done:
            return True
        try:
            await ensure_indexes(self._db)
            problems = await verify_indexes(self._db)
        except PyMongoError as exc:
            logger.warning("mongodb index setup failed", extra={"error_type": type(exc).__name__})
            return False
        if problems:
            logger.error(
                "mongodb indexes do not match the definition", extra={"problems": problems}
            )
            return False
        self._done = True
        logger.info("mongodb indexes ensured")
        return True

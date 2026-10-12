"""MongoDB client creation and health probing.

The client is created and closed by the application lifespan (see `docusearch.main`); nothing here
holds module-level state.
"""

import asyncio
import logging
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from docusearch.core.settings import Settings

logger = logging.getLogger(__name__)

type MongoClient = AsyncMongoClient[dict[str, Any]]
type MongoDatabase = AsyncDatabase[dict[str, Any]]


def create_client(settings: Settings) -> MongoClient:
    """Build the client. It does not connect until the first operation, so the application can
    start (and report not-ready) while MongoDB is down."""
    return AsyncMongoClient(
        settings.mongodb_uri.get_secret_value(),
        # Datetimes read from MongoDB are UTC-aware instead of naive.
        tz_aware=True,
        appname="docusearch",
        serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
        connectTimeoutMS=settings.mongodb_timeout_ms,
    )


def get_database(client: MongoClient, settings: Settings) -> MongoDatabase:
    return client[settings.mongodb_db_name]


async def ping(client: MongoClient, *, timeout_seconds: float) -> bool:
    """Readiness probe: True when the server answers `ping` within the timeout.

    Only the exception type is logged; driver messages can contain connection details.
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            await client.admin.command("ping")
    except (PyMongoError, TimeoutError) as exc:
        logger.warning("mongodb ping failed", extra={"error_type": type(exc).__name__})
        return False
    return True

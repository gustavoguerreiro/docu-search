"""FastAPI application factory.

Run locally with:  uvicorn docusearch.main:create_app --factory
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI

from docusearch import __version__
from docusearch.api import API_PREFIX, auth, documents, health
from docusearch.api.errors import install_error_handlers
from docusearch.api.limits import RequestSizeLimitMiddleware
from docusearch.auth.refresh_tokens import RefreshTokenService
from docusearch.auth.service import AuthService
from docusearch.core.logging_setup import configure_logging
from docusearch.core.settings import Settings, get_settings
from docusearch.db.indexes import IndexInitializer
from docusearch.db.mongo import create_client, get_database, ping
from docusearch.db.repositories.chunks import ChunkRepository
from docusearch.db.repositories.documents import DocumentRepository
from docusearch.db.repositories.refresh_tokens import RefreshTokenRepository
from docusearch.db.repositories.users import UserRepository
from docusearch.documents.service import DocumentService
from docusearch.jobs.redis_queue import RqJobQueue, create_redis
from docusearch.jobs.redis_queue import ping as redis_ping
from docusearch.storage.local import LocalStorage

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Process-level startup/shutdown: owns the MongoDB client (Redis joins it in Milestone 5)."""
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)

    mongo_client = create_client(settings)
    app.state.mongo_client = mongo_client
    app.state.mongo_db = get_database(mongo_client, settings)
    # Slightly above the driver's own timeout so the driver reports first.
    app.state.readiness_checks["mongodb"] = partial(
        ping, mongo_client, timeout_seconds=settings.mongodb_timeout_ms / 1000 + 1
    )

    # Like the MongoDB client, creating this does not connect: the API starts while Redis is down
    # and reports not-ready until it returns.
    redis_client = create_redis(settings)
    app.state.redis = redis_client
    app.state.job_queue = RqJobQueue.from_client(redis_client)
    app.state.readiness_checks["redis"] = partial(redis_ping, redis_client)

    # Created lazily on first use; the readiness check reports whether it is writable.
    storage = LocalStorage(settings.storage_local_root)
    app.state.storage = storage
    app.state.readiness_checks["storage"] = storage.healthy

    app.state.document_service = DocumentService(
        DocumentRepository(app.state.mongo_db),
        ChunkRepository(app.state.mongo_db),
        storage,
        max_upload_bytes=settings.max_upload_size_mb * 1024 * 1024,
    )

    refresh_tokens = RefreshTokenService(RefreshTokenRepository(app.state.mongo_db), settings)
    app.state.auth_service = AuthService(
        UserRepository(app.state.mongo_db), refresh_tokens, settings
    )

    # Try once now so constraints exist before the first request. If MongoDB is down the app still
    # starts; the `indexes` readiness check keeps retrying and reports not-ready until it succeeds.
    index_initializer = IndexInitializer(app.state.mongo_db)
    await index_initializer.ensure()
    app.state.readiness_checks["indexes"] = index_initializer.ensure

    logger.info(
        "application started",
        extra={
            "app_env": settings.app_env,
            "version": __version__,
            "mongodb_db": settings.mongodb_db_name,
        },
    )
    try:
        yield
    finally:
        await mongo_client.close()
        redis_client.close()
        logger.info("application stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else get_settings()

    app = FastAPI(
        title="DocuSearch",
        version=__version__,
        description="Document ingestion, indexing and search API.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.readiness_checks = {}

    install_error_handlers(app)
    app.add_middleware(
        RequestSizeLimitMiddleware,
        upload_path=f"{API_PREFIX}/documents",
        max_upload_bytes=settings.max_upload_size_mb * 1024 * 1024,
    )
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(documents.router, prefix=API_PREFIX)
    return app

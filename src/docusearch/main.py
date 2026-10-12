"""FastAPI application factory.

Run locally with:  uvicorn docusearch.main:create_app --factory
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI

from docusearch import __version__
from docusearch.api import health
from docusearch.core.logging_setup import configure_logging
from docusearch.core.settings import Settings, get_settings
from docusearch.db.indexes import IndexInitializer
from docusearch.db.mongo import create_client, get_database, ping

API_PREFIX = "/api/v1"

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

    app.include_router(health.router, prefix=API_PREFIX)
    return app

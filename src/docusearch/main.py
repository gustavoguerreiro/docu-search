"""FastAPI application factory.

Run locally with:  uvicorn docusearch.main:create_app --factory
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from docusearch import __version__
from docusearch.api import health
from docusearch.core.logging_setup import configure_logging
from docusearch.core.settings import Settings, get_settings

API_PREFIX = "/api/v1"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Process-level startup/shutdown. Database and Redis clients will be managed here."""
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    logger.info("application started", extra={"app_env": settings.app_env, "version": __version__})
    yield
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

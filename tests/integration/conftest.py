"""Fixtures for tests that need a real MongoDB.

Start the throwaway instance with `make test-integration` (or `make test-db-up`). By default tests
connect to mongodb://127.0.0.1:27018; override with MONGODB_TEST_URI.
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from pydantic import SecretStr

from docusearch.core.settings import Settings
from docusearch.db.mongo import MongoClient, MongoDatabase, create_client, ping

DEFAULT_TEST_URI = "mongodb://127.0.0.1:27018"
TEST_DB_PREFIX = "docusearch_test_"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Everything under tests/integration is an integration test, so `-m "not integration"`
    (used by `make test` and CI) reliably excludes it."""
    for item in items:
        if "tests/integration/" in item.path.as_posix():
            item.add_marker(pytest.mark.integration)


@pytest.fixture
def mongo_uri() -> str:
    return os.environ.get("MONGODB_TEST_URI", DEFAULT_TEST_URI)


@pytest.fixture
def test_db_name() -> str:
    """A unique database per test, so tests can never see each other's data."""
    return f"{TEST_DB_PREFIX}{uuid.uuid4().hex[:12]}"


@pytest.fixture
def mongo_settings(mongo_uri: str, test_db_name: str) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        mongodb_uri=SecretStr(mongo_uri),
        mongodb_db_name=test_db_name,
        mongodb_timeout_ms=2000,
    )


@pytest.fixture
async def mongo_client(mongo_settings: Settings) -> AsyncIterator[MongoClient]:
    client = create_client(mongo_settings)
    try:
        if not await ping(client, timeout_seconds=5):
            # Fail loudly rather than skip: a silently skipped suite would look like a pass.
            pytest.fail(
                "Test MongoDB is not reachable at the configured URI. "
                "Run `make test-integration`, or `make test-db-up` and retry "
                "(override the URI with MONGODB_TEST_URI)."
            )
        yield client
    finally:
        await client.close()


@pytest.fixture
async def mongo_db(
    mongo_client: MongoClient, mongo_settings: Settings
) -> AsyncIterator[MongoDatabase]:
    name = mongo_settings.mongodb_db_name
    # Never drop a database this suite did not create.
    assert name.startswith(TEST_DB_PREFIX)
    db = mongo_client[name]
    try:
        yield db
    finally:
        await mongo_client.drop_database(name)

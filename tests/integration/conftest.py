"""Fixtures for tests that need a real MongoDB.

Start the throwaway instance with `make test-integration` (or `make test-db-up`). By default tests
connect to mongodb://127.0.0.1:27018; override with MONGODB_TEST_URI.
"""

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from redis import Redis
from redis.exceptions import RedisError

from docusearch.core.settings import Settings
from docusearch.db.mongo import MongoClient, MongoDatabase, create_client, ping

DEFAULT_TEST_URI = "mongodb://127.0.0.1:27018"
DEFAULT_REDIS_TEST_URL = "redis://127.0.0.1:6380/0"
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
def redis_url() -> str:
    return os.environ.get("REDIS_TEST_URL", DEFAULT_REDIS_TEST_URL)


@pytest.fixture
def queue_name() -> str:
    """A queue per test, so jobs of different tests never meet."""
    return f"docusearch-test-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def test_db_name() -> str:
    """A unique database per test, so tests can never see each other's data."""
    return f"{TEST_DB_PREFIX}{uuid.uuid4().hex[:12]}"


@pytest.fixture
def mongo_settings(mongo_uri: str, redis_url: str, test_db_name: str, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        storage_local_root=tmp_path / "uploads",
        redis_url=SecretStr(redis_url),
        redis_timeout_ms=2000,
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


@pytest.fixture
def redis_client(redis_url: str) -> Iterator[Redis]:
    """A real client against the throwaway Redis. Unreachable means fail, not skip."""
    client = Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        try:
            client.ping()
        except RedisError:
            pytest.fail(
                "Test Redis is not reachable at the configured URL. "
                "Run `make test-integration`, or `make test-db-up` and retry "
                "(override the URL with REDIS_TEST_URL)."
            )
        yield client
    finally:
        client.close()

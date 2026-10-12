import json
import logging

import pytest

from docusearch import __version__
from docusearch.core.settings import Settings, get_settings
from docusearch.main import create_app


def test_create_app_uses_the_given_settings() -> None:
    settings = Settings(_env_file=None, app_env="test")

    app = create_app(settings)

    assert app.state.settings is settings
    assert app.title == "DocuSearch"
    assert app.version == __version__


def test_create_app_falls_back_to_cached_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    try:
        app = create_app()

        assert app.state.settings is get_settings()
        assert app.state.settings.app_env == "test"
    finally:
        get_settings.cache_clear()


def test_create_app_starts_with_no_readiness_checks() -> None:
    assert create_app(Settings(_env_file=None)).state.readiness_checks == {}


async def test_lifespan_configures_json_logging(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app(Settings(_env_file=None, log_level="WARNING"))

    async with app.router.lifespan_context(app):
        assert logging.getLogger().level == logging.WARNING
        logging.getLogger("docusearch.test").warning("during run")

    messages = [json.loads(line)["message"] for line in capsys.readouterr().out.splitlines()]
    assert messages == ["during run"]  # INFO start/stop lines are filtered at WARNING


async def test_lifespan_logs_start_and_stop(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app(Settings(_env_file=None, log_level="INFO"))

    async with app.router.lifespan_context(app):
        pass

    entries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e["message"] for e in entries] == ["application started", "application stopped"]
    assert entries[0]["version"] == __version__
    assert entries[0]["app_env"] == "local"


class SpyMongoClient:
    def __init__(self) -> None:
        self.closed = False
        self.pings = 0
        self.admin = self

    async def command(self, name: str) -> dict[str, float]:
        self.pings += 1
        return {"ok": 1.0}

    def __getitem__(self, name: str) -> str:
        return f"db:{name}"

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def spy_client(monkeypatch: pytest.MonkeyPatch) -> SpyMongoClient:
    spy = SpyMongoClient()
    monkeypatch.setattr("docusearch.main.create_client", lambda settings: spy)
    return spy


async def test_lifespan_owns_the_mongo_client(spy_client: SpyMongoClient) -> None:
    app = create_app(Settings(_env_file=None, mongodb_db_name="lifecycle_db"))

    async with app.router.lifespan_context(app):
        assert app.state.mongo_client is spy_client
        assert app.state.mongo_db == "db:lifecycle_db"
        assert not spy_client.closed

    assert spy_client.closed


async def test_lifespan_closes_the_client_even_if_the_app_fails(spy_client: SpyMongoClient) -> None:
    app = create_app(Settings(_env_file=None))

    with pytest.raises(RuntimeError, match="boom"):
        async with app.router.lifespan_context(app):
            raise RuntimeError("boom")

    assert spy_client.closed


async def test_lifespan_registers_a_working_mongodb_readiness_check(
    spy_client: SpyMongoClient,
) -> None:
    app = create_app(Settings(_env_file=None))

    async with app.router.lifespan_context(app):
        check = app.state.readiness_checks["mongodb"]
        assert await check() is True

    assert spy_client.pings == 1

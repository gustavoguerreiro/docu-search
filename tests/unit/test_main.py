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

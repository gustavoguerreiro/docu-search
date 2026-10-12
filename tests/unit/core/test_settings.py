from pathlib import Path

import pytest
from pydantic import ValidationError

from docusearch.core.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_ENV", "LOG_LEVEL", "MONGODB_URI", "MONGODB_DB_NAME", "MONGODB_TIMEOUT_MS"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_need_no_configuration() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "local"
    assert settings.log_level == "INFO"


def test_reads_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    settings = Settings(_env_file=None)

    assert settings.app_env == "production"
    assert settings.log_level == "WARNING"


def test_log_level_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", " debug ")

    assert Settings(_env_file=None).log_level == "DEBUG"


@pytest.mark.parametrize(("name", "value"), [("APP_ENV", "staging"), ("LOG_LEVEL", "LOUD")])
def test_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_environment_overrides_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=test\nLOG_LEVEL=ERROR\nUNKNOWN_VARIABLE=ignored\n")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=env_file)

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"


def test_settings_are_immutable() -> None:
    settings = Settings(_env_file=None)

    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"  # type: ignore[misc]


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()

    assert get_settings() is get_settings()

    get_settings.cache_clear()


def test_mongodb_defaults_work_without_configuration() -> None:
    settings = Settings(_env_file=None)

    assert settings.mongodb_uri.get_secret_value() == "mongodb://localhost:27017"
    assert settings.mongodb_db_name == "docusearch"
    assert settings.mongodb_timeout_ms == 2000


def test_mongodb_uri_is_hidden_from_repr_and_dumps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://user:hunter2@db:27017")

    settings = Settings(_env_file=None)

    assert settings.mongodb_uri.get_secret_value() == "mongodb://user:hunter2@db:27017"
    assert "hunter2" not in repr(settings)
    assert "hunter2" not in str(settings.model_dump())
    assert "hunter2" not in settings.model_dump_json()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MONGODB_DB_NAME", "bad name"),
        ("MONGODB_DB_NAME", "a.b"),
        ("MONGODB_DB_NAME", ""),
        ("MONGODB_TIMEOUT_MS", "10"),
        ("MONGODB_TIMEOUT_MS", "600000"),
    ],
)
def test_rejects_invalid_mongodb_settings(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)

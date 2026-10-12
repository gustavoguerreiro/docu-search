from pathlib import Path

import pytest
from pydantic import ValidationError

from docusearch.core.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "APP_ENV",
        "LOG_LEVEL",
        "MONGODB_URI",
        "MONGODB_DB_NAME",
        "MONGODB_TIMEOUT_MS",
        "JWT_SECRET_KEY",
        "JWT_ALGORITHM",
        "ACCESS_TOKEN_TTL_MINUTES",
        "REFRESH_TOKEN_TTL_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_need_no_configuration() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "local"
    assert settings.log_level == "INFO"


def test_reads_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "k" * 40)  # production requires an explicit key
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


GOOD_KEY = "s" * 40


def test_jwt_key_defaults_to_a_random_per_instance_secret_outside_production() -> None:
    first, second = Settings(_env_file=None), Settings(_env_file=None)

    assert len(first.jwt_secret_key.get_secret_value()) >= 32
    assert first.jwt_secret_key.get_secret_value() != second.jwt_secret_key.get_secret_value()


def test_jwt_key_is_read_from_the_environment_and_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", GOOD_KEY)

    settings = Settings(_env_file=None)

    assert settings.jwt_secret_key.get_secret_value() == GOOD_KEY
    assert GOOD_KEY not in repr(settings)
    assert GOOD_KEY not in settings.model_dump_json()


@pytest.mark.parametrize("app_env", ["local", "test", "production"])
def test_an_explicit_jwt_key_must_be_long_enough_in_every_environment(
    monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("JWT_SECRET_KEY", "too-short")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_an_empty_jwt_key_is_not_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_production_refuses_to_start_without_an_explicit_jwt_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(ValidationError, match="JWT_SECRET_KEY must be set"):
        Settings(_env_file=None)


@pytest.mark.parametrize("placeholder", ["change-me-" + "x" * 30, "CHANGE-ME-" + "x" * 30])
def test_production_refuses_placeholder_keys(
    monkeypatch: pytest.MonkeyPatch, placeholder: str
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", placeholder)

    with pytest.raises(ValidationError, match="placeholder"):
        Settings(_env_file=None)


def test_production_accepts_a_proper_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", GOOD_KEY)

    assert Settings(_env_file=None).app_env == "production"


def test_only_hs256_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None).jwt_algorithm == "HS256"
    for algorithm in ("none", "HS512", "RS256", ""):
        monkeypatch.setenv("JWT_ALGORITHM", algorithm)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)


@pytest.mark.parametrize("minutes", ["0", "61", "-5"])
def test_access_token_ttl_is_bounded(monkeypatch: pytest.MonkeyPatch, minutes: str) -> None:
    monkeypatch.setenv("ACCESS_TOKEN_TTL_MINUTES", minutes)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_refresh_token_ttl_defaults_and_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None).refresh_token_ttl_days == 14

    for days in ("0", "91", "-1"):
        monkeypatch.setenv("REFRESH_TOKEN_TTL_DAYS", days)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    monkeypatch.setenv("REFRESH_TOKEN_TTL_DAYS", "30")
    assert Settings(_env_file=None).refresh_token_ttl_days == 30

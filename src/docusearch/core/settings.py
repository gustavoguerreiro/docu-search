"""Application configuration, read from environment variables (and an optional `.env` file).

This is the only place configuration is defined. Each milestone adds the fields it needs.
"""

import secrets
from functools import lru_cache
from typing import Any, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# HS256 needs a key of at least 256 bits (RFC 7518 section 3.2).
_MIN_JWT_SECRET_LENGTH = 32

type AppEnv = Literal["local", "test", "production"]
type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _random_secret() -> SecretStr:
    return SecretStr(secrets.token_urlsafe(48))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_env: AppEnv = "local"
    log_level: LogLevel = "INFO"

    # The URI may embed credentials, hence SecretStr (hidden from repr and logs).
    mongodb_uri: SecretStr = SecretStr("mongodb://localhost:27017")
    mongodb_db_name: str = Field(default="docusearch", pattern=r"^[A-Za-z0-9_-]{1,38}$")
    # Bounds server selection and connection attempts, so a dead database fails fast.
    mongodb_timeout_ms: int = Field(default=2000, ge=100, le=60_000)

    # Signing key for access tokens. When unset outside production, a random key is generated for
    # this process: there is no public default to forge tokens with, but tokens do not survive a
    # restart and differ between processes. Production must provide one (see the validator below).
    jwt_secret_key: SecretStr = Field(
        default_factory=_random_secret, min_length=_MIN_JWT_SECRET_LENGTH
    )
    # Only HMAC-SHA256 is supported; the literal type keeps "none" and other algorithms unreachable.
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=60)

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _require_production_secrets(self) -> Self:
        if self.app_env == "production":
            if "jwt_secret_key" not in self.model_fields_set:
                raise ValueError("JWT_SECRET_KEY must be set when APP_ENV=production")
            if self.jwt_secret_key.get_secret_value().lower().startswith("change-me"):
                raise ValueError("JWT_SECRET_KEY must not be a placeholder value")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, loaded once on first use."""
    return Settings()

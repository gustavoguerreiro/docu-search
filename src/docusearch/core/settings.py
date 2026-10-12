"""Application configuration, read from environment variables (and an optional `.env` file).

This is the only place configuration is defined. Each milestone adds the fields it needs.
"""

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

type AppEnv = Literal["local", "test", "production"]
type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


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

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, loaded once on first use."""
    return Settings()

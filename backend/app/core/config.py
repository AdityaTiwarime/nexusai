"""Application settings, loaded from environment variables (and an optional .env file).

Every setting is validated at startup, so a missing or malformed value fails fast
instead of surfacing later as a confusing runtime error.
"""

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXUS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "NexusAI"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    # postgresql+asyncpg://user:password@host:port/database
    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://nexus:nexus@localhost:5432/nexusai"
    )
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_health_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # Comma-separated in the environment, e.g. "http://localhost:5173,http://localhost:3000".
    # NoDecode stops pydantic-settings from requiring JSON here, so `split_origins` gets
    # the raw string (a JSON list like '["http://a"]' is still accepted).
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    api_prefix: str = "/api/v1"

    @field_validator("database_url")
    @classmethod
    def require_async_driver(cls, value: PostgresDsn) -> PostgresDsn:
        if value.scheme != "postgresql+asyncpg":
            raise ValueError("database_url must use the 'postgresql+asyncpg' driver")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            if value.strip().startswith("["):
                return json.loads(value)
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()

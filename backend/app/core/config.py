"""Application settings, loaded from environment variables (and an optional .env file).

Every setting is validated at startup, so a missing or malformed value fails fast
instead of surfacing later as a confusing runtime error.
"""

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Only for local development and tests. Startup refuses it in staging/production.
DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-0123456789"


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

    # --- Authentication ---
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_issuer: str = "nexusai"
    access_token_minutes: int = Field(default=15, ge=1, le=120)
    refresh_token_days: int = Field(default=7, ge=1, le=90)
    # The refresh token lives in an httpOnly cookie. `secure` must be on wherever HTTPS is used.
    refresh_cookie_name: str = "nexus_refresh"
    cookie_secure: bool = False
    # Lets anyone create a new organization (fine for a demo; turn off for private deployments).
    allow_public_signup: bool = True

    @model_validator(mode="after")
    def check_production_security(self) -> "Settings":
        if self.environment in ("staging", "production"):
            secret = self.jwt_secret.get_secret_value()
            if secret == DEV_JWT_SECRET or len(secret) < 32:
                raise ValueError("Set NEXUS_JWT_SECRET to a random value of at least 32 characters")
            if not self.cookie_secure:
                raise ValueError("NEXUS_COOKIE_SECURE must be true in staging/production")
        return self

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

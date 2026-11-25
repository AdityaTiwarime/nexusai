"""Application settings, loaded from environment variables (and an optional .env file).

Every setting is validated at startup, so a missing or malformed value fails fast
instead of surfacing later as a confusing runtime error.
"""

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Only for local development and tests. Startup refuses them in staging/production.
DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-0123456789"
DEV_ENCRYPTION_KEY = (
    "ZGV2LW9ubHktbmV4dXNhaS1lbmNyeXB0aW9uLWtleSE="  # Fernet key (base64 of 32 bytes)
)


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

    # --- Redis (rate limiting) ---
    redis_url: str = "redis://localhost:6379/0"
    # If Redis is down, requests are allowed (fail open) and a warning is logged.
    rate_limit_enabled: bool = True
    login_rate_limit_per_ip: int = Field(default=20, ge=1)  # attempts per minute per IP
    login_failures_per_account: int = Field(default=5, ge=1)  # failures per 15 min per email
    register_rate_limit_per_ip: int = Field(default=10, ge=1)  # sign-ups per hour per IP

    # --- Documents ---
    storage_dir: str = "./data/uploads"
    max_upload_mb: int = Field(default=20, ge=1, le=200)
    chunk_size: int = Field(default=1200, ge=200, le=8000)  # characters
    chunk_overlap: int = Field(default=200, ge=0, le=2000)  # characters

    # --- Embeddings ---
    # "hashing" = deterministic, offline, NOT semantic (dev/tests). "openai" = real embeddings.
    embedding_provider: Literal["hashing", "openai"] = "hashing"
    embedding_model: str = "text-embedding-3-small"
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    embedding_batch_size: int = Field(default=64, ge=1, le=512)
    embedding_timeout_seconds: float = Field(default=30, gt=0, le=300)

    # --- Retrieval and answers (RAG) ---
    # "extractive" = offline, no LLM: quotes the best-matching sentences from the sources.
    # "openai" = an OpenAI-compatible chat model writes the answer from the sources.
    answer_provider: Literal["extractive", "openai"] = "extractive"
    chat_model: str = "gpt-4o-mini"
    chat_timeout_seconds: float = Field(default=60, gt=0, le=300)
    chat_max_output_tokens: int = Field(default=700, ge=50, le=4000)
    retrieval_candidates: int = Field(default=30, ge=5, le=200)  # per search mode, before fusion
    retrieval_top_k: int = Field(default=6, ge=1, le=20)  # passages given to the answer step
    # Passages below this cosine similarity count as evidence only if they also match keywords.
    retrieval_min_similarity: float = Field(default=0.3, ge=0, le=1)
    chat_rate_limit_per_user: int = Field(default=30, ge=1)  # questions per minute

    # --- Agents and approvals ---
    # "rules" = offline keyword planner (limited, predictable). "openai" = an LLM writes the
    # plan as JSON; either way the plan is validated against the tool registry.
    agent_planner: Literal["rules", "openai"] = "rules"
    agent_max_steps: int = Field(default=5, ge=1, le=10)
    approval_ttl_hours: int = Field(default=24, ge=1, le=720)
    agent_rate_limit_per_user: int = Field(default=10, ge=1)  # new runs per minute

    # --- Integrations (Phase 6) ---
    # Encrypts connector credentials at rest. Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: SecretStr = SecretStr(DEV_ENCRYPTION_KEY)
    # Connectors may only call these hosts (protects against SSRF through a configured URL).
    integration_allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "mock-services"]
    )
    integration_timeout_seconds: float = Field(default=10, gt=0, le=120)
    integration_max_attempts: int = Field(default=3, ge=1, le=10)
    integration_retry_base_seconds: float = Field(default=0.5, ge=0, le=30)
    integration_max_retry_after_seconds: float = Field(default=10, ge=0, le=300)
    # Base URL other systems use to reach this API (for webhook URLs).
    public_api_url: str = "http://localhost:8000"

    # --- Background worker ---
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    job_retry_base_seconds: float = Field(default=5.0, ge=0)
    job_lock_timeout_seconds: int = Field(default=300, ge=10)  # reclaim jobs from crashed workers
    # Touched by the worker on every loop; docker-compose's healthcheck reads its age.
    worker_heartbeat_file: str = "/tmp/nexus-worker-heartbeat"

    @model_validator(mode="after")
    def check_documents_and_embeddings(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        uses_openai = "openai" in (
            self.embedding_provider,
            self.answer_provider,
            self.agent_planner,
        )
        if uses_openai and not self.openai_api_key:
            raise ValueError("NEXUS_OPENAI_API_KEY is required when an OpenAI provider is selected")
        return self

    @model_validator(mode="after")
    def check_production_security(self) -> "Settings":
        if self.environment in ("staging", "production"):
            secret = self.jwt_secret.get_secret_value()
            if secret == DEV_JWT_SECRET or len(secret) < 32:
                raise ValueError("Set NEXUS_JWT_SECRET to a random value of at least 32 characters")
            if not self.cookie_secure:
                raise ValueError("NEXUS_COOKIE_SECURE must be true in staging/production")
            if self.encryption_key.get_secret_value() == DEV_ENCRYPTION_KEY:
                raise ValueError("Set NEXUS_ENCRYPTION_KEY to a generated Fernet key")
            # docker compose's local default; a real deployment generates one (Terraform does).
            if self.database_url.hosts()[0].get("password") in (None, "", "nexus"):
                raise ValueError("Set a real database password in NEXUS_DATABASE_URL")
        return self

    @field_validator("database_url")
    @classmethod
    def require_async_driver(cls, value: PostgresDsn) -> PostgresDsn:
        if value.scheme != "postgresql+asyncpg":
            raise ValueError("database_url must use the 'postgresql+asyncpg' driver")
        return value

    @field_validator("integration_allowed_hosts", mode="before")
    @classmethod
    def split_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [h.strip().lower() for h in value.split(",") if h.strip()]
        return value

    @field_validator("encryption_key")
    @classmethod
    def valid_fernet_key(cls, value: SecretStr) -> SecretStr:
        from cryptography.fernet import Fernet

        try:
            Fernet(value.get_secret_value().encode())
        except (ValueError, TypeError):
            raise ValueError(
                "NEXUS_ENCRYPTION_KEY must be a Fernet key (32 url-safe base64 bytes)"
            ) from None
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

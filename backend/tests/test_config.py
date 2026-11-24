import pytest
from pydantic import ValidationError

from app.core.config import DEV_ENCRYPTION_KEY, Settings
from tests.conftest import make_settings


def test_rejects_non_async_database_driver():
    with pytest.raises(ValidationError):
        make_settings(database_url="postgresql://nexus:nexus@localhost:5432/nexusai")


def test_cors_origins_accepts_comma_separated_string():
    settings = make_settings(cors_origins="http://a.test, http://b.test")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:5173", ["http://localhost:5173"]),
        ("http://a.test,http://b.test", ["http://a.test", "http://b.test"]),
        ('["http://a.test", "http://b.test"]', ["http://a.test", "http://b.test"]),
    ],
)
def test_settings_load_from_environment_like_docker(monkeypatch, raw, expected):
    """Docker passes settings as plain environment variables; startup must accept them."""
    from app.core.config import Settings

    monkeypatch.setenv("NEXUS_CORS_ORIGINS", raw)
    monkeypatch.setenv("NEXUS_DATABASE_URL", "postgresql+asyncpg://nexus:nexus@db:5432/nexusai")
    monkeypatch.setenv("NEXUS_ENVIRONMENT", "local")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == expected
    assert "@db:5432/nexusai" in str(settings.database_url)


PROD = {
    "environment": "production",
    "jwt_secret": "x" * 40,
    "cookie_secure": True,
    "encryption_key": "cHJvZC10ZXN0LWtleS1ub3QtdGhlLWRldi1vbmUtMTI=",
}


def test_production_flag():
    assert make_settings(**PROD).is_production
    assert not make_settings(environment="local").is_production


def test_docs_disabled_in_production():
    from app.main import create_app

    app = create_app(make_settings(**PROD))
    assert app.docs_url is None
    assert app.openapi_url is None


def test_production_refuses_the_dev_encryption_key():
    with pytest.raises(ValueError, match="NEXUS_ENCRYPTION_KEY"):
        make_settings(**{**PROD, "encryption_key": DEV_ENCRYPTION_KEY})


def test_encryption_key_must_be_a_fernet_key():
    with pytest.raises(ValueError, match="Fernet key"):
        make_settings(encryption_key="not-a-key")


def test_integration_hosts_from_env(monkeypatch):
    monkeypatch.setenv("NEXUS_INTEGRATION_ALLOWED_HOSTS", "mock-services, API.Example.com")
    assert Settings(_env_file=None).integration_allowed_hosts == [
        "mock-services",
        "api.example.com",
    ]

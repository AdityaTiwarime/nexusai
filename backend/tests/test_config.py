import pytest
from pydantic import ValidationError

from tests.conftest import make_settings


def test_rejects_non_async_database_driver():
    with pytest.raises(ValidationError):
        make_settings(database_url="postgresql://nexus:nexus@localhost:5432/nexusai")


def test_cors_origins_accepts_comma_separated_string():
    settings = make_settings(cors_origins="http://a.test, http://b.test")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_production_flag():
    assert make_settings(environment="production").is_production
    assert not make_settings(environment="local").is_production


def test_docs_disabled_in_production():
    from app.main import create_app

    app = create_app(make_settings(environment="production"))
    assert app.docs_url is None
    assert app.openapi_url is None

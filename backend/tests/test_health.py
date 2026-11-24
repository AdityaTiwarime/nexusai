from app.main import create_app
from tests.conftest import make_settings

API = "/api/v1"


async def test_liveness_is_ok(client):
    response = await client.get(f"{API}/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_reports_database_ok(client):
    response = await client.get(f"{API}/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["database"]["latency_ms"] >= 0


async def test_readiness_returns_503_when_database_is_down(client_factory):
    # Port 1 has nothing listening, so the connection fails fast.
    settings = make_settings(
        database_url="postgresql+asyncpg://nexus:secret-password@127.0.0.1:1/nexusai",
        db_health_timeout_seconds=2,
    )
    async for client in client_factory(create_app(settings)):
        response = await client.get(f"{API}/health/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "error"
        assert body["checks"]["database"]["status"] == "error"
        # Only the error type is exposed, never connection details.
        assert "secret-password" not in response.text
        assert "127.0.0.1" not in response.text


async def test_liveness_stays_ok_when_database_is_down(client_factory):
    settings = make_settings(database_url="postgresql+asyncpg://nexus:x@127.0.0.1:1/nexusai")
    async for client in client_factory(create_app(settings)):
        response = await client.get(f"{API}/health/live")
        assert response.status_code == 200


async def test_system_info(client):
    response = await client.get(f"{API}/system/info")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "NexusAI"
    assert body["environment"] == "test"
    assert body["version"]
    assert body["answer_provider"] == "extractive"
    assert body["chat_model"] is None
    assert body["embedding_provider"] == "hashing"

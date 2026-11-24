from app.main import create_app
from tests.conftest import make_settings

API = "/api/v1"


async def test_request_id_is_generated(client):
    response = await client.get(f"{API}/health/live")
    request_id = response.headers.get("x-request-id")
    assert request_id and len(request_id) == 32


async def test_safe_incoming_request_id_is_echoed(client):
    response = await client.get(f"{API}/health/live", headers={"X-Request-ID": "trace-abc_123"})
    assert response.headers["x-request-id"] == "trace-abc_123"


async def test_unsafe_incoming_request_id_is_replaced(client):
    response = await client.get(
        f"{API}/health/live", headers={"X-Request-ID": "bad id with spaces<script>"}
    )
    assert response.headers["x-request-id"] != "bad id with spaces<script>"
    assert len(response.headers["x-request-id"]) == 32


async def test_unhandled_error_returns_500_with_request_id(client_factory):
    app = create_app(make_settings())

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("internal detail that must not leak")

    async for client in client_factory(app):
        response = await client.get("/boom", headers={"X-Request-ID": "req-500"})
        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error", "request_id": "req-500"}
        assert "internal detail" not in response.text


async def test_cors_allows_configured_origin(client):
    response = await client.options(
        f"{API}/health/live",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


async def test_cors_rejects_unknown_origin(client):
    response = await client.options(
        f"{API}/health/live",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in response.headers

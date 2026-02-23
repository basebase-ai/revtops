from fastapi.testclient import TestClient

from api.main import app
from config import settings


class _MockResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _MockAsyncClient:
    def __init__(self, *args, **kwargs):
        self.response = kwargs.pop("_response")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        return self.response


def test_password_reset_request_success(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", "anon-key")

    import api.routes.auth as auth_routes

    monkeypatch.setattr(
        auth_routes.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _MockAsyncClient(_response=_MockResponse(200)),
    )

    client = TestClient(app)
    response = client.post("/api/auth/password-reset/request", json={"email": "user@company.com"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True


def test_password_reset_request_upstream_failure(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", "anon-key")

    import api.routes.auth as auth_routes

    monkeypatch.setattr(
        auth_routes.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _MockAsyncClient(_response=_MockResponse(500, "boom")),
    )

    client = TestClient(app)
    response = client.post("/api/auth/password-reset/request", json={"email": "user@company.com"})

    assert response.status_code == 502

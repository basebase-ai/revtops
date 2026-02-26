import pytest
from fastapi import HTTPException

from api import auth_middleware as am


class _FailingResponse:
    def raise_for_status(self) -> None:
        raise RuntimeError("network down")

    def json(self) -> dict:
        return {}


class _FailingClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str) -> _FailingResponse:
        return _FailingResponse()


@pytest.mark.asyncio
async def test_get_jwks_uses_stale_cache_when_refresh_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(am.settings, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(am.httpx, "AsyncClient", _FailingClient)

    am._jwks_cache = {"keys": [{"kid": "cached-key"}]}
    am._jwks_cache_fetched_at = 0.0  # force stale cache path

    jwks = await am._get_jwks()

    assert jwks == {"keys": [{"kid": "cached-key"}]}


@pytest.mark.asyncio
async def test_get_jwks_raises_503_without_cache_when_refresh_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(am.settings, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(am.httpx, "AsyncClient", _FailingClient)

    am._jwks_cache = None
    am._jwks_cache_fetched_at = None

    with pytest.raises(HTTPException) as exc_info:
        await am._get_jwks()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Authentication service temporarily unavailable"


@pytest.mark.asyncio
async def test_get_jwks_raises_incident_after_final_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(am.settings, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(am.httpx, "AsyncClient", _FailingClient)

    calls: list[tuple[str, str]] = []

    async def _fake_create_pagerduty_incident(*, title: str, details: str) -> bool:
        calls.append((title, details))
        return True

    monkeypatch.setattr(am, "create_pagerduty_incident", _fake_create_pagerduty_incident)
    am._jwks_cache = None
    am._jwks_cache_fetched_at = None

    with pytest.raises(HTTPException):
        await am._get_jwks()

    assert len(calls) == 1
    assert calls[0][0] == "Auth JWKS endpoint unreachable"

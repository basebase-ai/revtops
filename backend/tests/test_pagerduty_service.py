from __future__ import annotations

from typing import Any

from config import settings
from services import pagerduty


class _FakeResponse:
    def __init__(self, status_code: int = 201, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
        return _FakeResponse()


class _FakeAsyncClient500(_FakeAsyncClient):
    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
        return _FakeResponse(status_code=500, text="upstream error")


def test_create_pagerduty_incident_with_details_missing_config(monkeypatch: Any) -> None:
    monkeypatch.delenv("PAGERDUTY_FROM_EMAIL", raising=False)
    monkeypatch.delenv("PAGERDUTY_KEY", raising=False)
    monkeypatch.delenv("PagerDuty_Key", raising=False)
    monkeypatch.delenv("PAGERDUTY_SERVICE_ID", raising=False)
    monkeypatch.setattr(pagerduty, "settings", settings.__class__())

    import asyncio

    result = asyncio.run(
        pagerduty.create_pagerduty_incident_with_details(
            title="test",
            details="test",
        )
    )

    assert result.ok is False
    assert result.reason == "missing_config"


def test_create_pagerduty_incident_with_details_http_error(monkeypatch: Any) -> None:
    monkeypatch.setattr(pagerduty.httpx, "AsyncClient", _FakeAsyncClient500)
    monkeypatch.setenv("PAGERDUTY_FROM_EMAIL", "alerts@revtops.com")
    monkeypatch.setenv("PagerDuty_Key", "pd_test_key")
    monkeypatch.setenv("PAGERDUTY_SERVICE_ID", "svc_123")
    monkeypatch.setenv("FRONTEND_URL", "https://app.basebase.com")
    monkeypatch.delenv("BACKEND_PUBLIC_URL", raising=False)
    monkeypatch.setattr(pagerduty, "settings", settings.__class__())

    import asyncio

    result = asyncio.run(
        pagerduty.create_pagerduty_incident_with_details(
            title="test",
            details="test",
        )
    )

    assert result.ok is False
    assert result.reason == "http_error"
    assert result.status_code == 500


def test_create_pagerduty_incident_with_details_success(monkeypatch: Any) -> None:
    monkeypatch.setattr(pagerduty.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setenv("PAGERDUTY_FROM_EMAIL", "alerts@revtops.com")
    monkeypatch.setenv("PagerDuty_Key", "pd_test_key")
    monkeypatch.setenv("PAGERDUTY_SERVICE_ID", "svc_123")
    monkeypatch.setenv("FRONTEND_URL", "https://app.basebase.com")
    monkeypatch.delenv("BACKEND_PUBLIC_URL", raising=False)
    monkeypatch.setattr(pagerduty, "settings", settings.__class__())

    import asyncio

    result = asyncio.run(
        pagerduty.create_pagerduty_incident_with_details(
            title="test",
            details="test",
        )
    )

    assert result.ok is True
    assert result.reason == "created"
    assert result.status_code == 201


def test_get_pagerduty_config_skips_when_frontend_url_is_localhost(monkeypatch: Any) -> None:
    monkeypatch.setenv("PAGERDUTY_FROM_EMAIL", "alerts@revtops.com")
    monkeypatch.setenv("PagerDuty_Key", "pd_test_key")
    monkeypatch.setenv("PAGERDUTY_SERVICE_ID", "svc_123")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")
    monkeypatch.delenv("BACKEND_PUBLIC_URL", raising=False)
    monkeypatch.setattr(pagerduty, "settings", settings.__class__())

    config = pagerduty.get_pagerduty_config()

    assert config is None


def test_get_pagerduty_config_skips_when_backend_public_url_is_localhost(monkeypatch: Any) -> None:
    monkeypatch.setenv("PAGERDUTY_FROM_EMAIL", "alerts@revtops.com")
    monkeypatch.setenv("PagerDuty_Key", "pd_test_key")
    monkeypatch.setenv("PAGERDUTY_SERVICE_ID", "svc_123")
    monkeypatch.setenv("FRONTEND_URL", "https://app.basebase.com")
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr(pagerduty, "settings", settings.__class__())

    config = pagerduty.get_pagerduty_config()

    assert config is None

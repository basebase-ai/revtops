"""
Tests for iSpot.tv connector. No real OAuth credentials required; mocks token and API.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from connectors.ispot_tv import ISpotTvConnector


def _fake_integration() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        is_active=True,
        pending_sharing_config=False,
        last_sync_at=None,
        extra_data={
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
        },
        updated_at=None,
        created_at=None,
    )


class _FakeIspotResponse:
    def __init__(self, status_code: int, json_data: dict) -> None:
        self.status_code = status_code
        self._json = json_data
        self.request = SimpleNamespace(url="https://api.ispot.tv/v4/")
        self.text = ""

    def json(self) -> dict:
        return self._json


class _FakeIspotHttpClient:
    """Fake httpx.AsyncClient that returns token for token URL and JSON data for API."""

    async def __aenter__(self) -> "_FakeIspotHttpClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        data: dict | None = None,
        headers: dict | None = None,
        timeout: float | None = None,
    ) -> _FakeIspotResponse:
        if "oauth2/token" in url:
            return _FakeIspotResponse(
                200,
                {"access_token": "mock-token-123", "expires_in": 86400},
            )
        return _FakeIspotResponse(400, {"error": "unexpected post"})

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> _FakeIspotResponse:
        if "brands" in url:
            return _FakeIspotResponse(
                200,
                {
                    "data": [
                        {
                            "id": "brand-1",
                            "attributes": {"name": "Test Brand", "industry_id": 407},
                        },
                    ],
                },
            )
        if "metrics/tv/airings" in url:
            return _FakeIspotResponse(
                200,
                {
                    "data": [
                        {
                            "id": "airing-1",
                            "attributes": {
                                "spot_name": "Test Spot",
                                "airing_date_et": "2025-01-15T20:00:00Z",
                                "est_spend": 50000,
                                "network": "ABC",
                                "show": "Show Name",
                                "duration": 30,
                                "airing_type": "N",
                                "brand_id": "brand-1",
                                "spot_id": "spot-1",
                            },
                        },
                    ],
                },
            )
        return _FakeIspotResponse(200, {"data": [], "meta": {}})


class _FakeSession:
    def __init__(self, integration: SimpleNamespace) -> None:
        self._integration = integration

    async def execute(self, query: object) -> object:
        class _Scalars:
            def all(self) -> list:
                return [self._integration]

            def __init__(_self: object, integration: SimpleNamespace) -> None:
                _self._integration = integration  # type: ignore[attr-defined]

        class _Result:
            def scalars(self) -> _Scalars:
                return _Scalars(self._integration)

            def __init__(_self: object, integration: SimpleNamespace) -> None:
                _self._integration = integration  # type: ignore[attr-defined]

        return _Result(self._integration)


class _FakeSessionContext:
    def __init__(self, integration: SimpleNamespace) -> None:
        self._session = _FakeSession(integration)

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_ispot_tv_token_and_sync_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    """With mocked HTTP and DB: fetch token, sync accounts returns AccountRecords."""
    integration = _fake_integration()
    monkeypatch.setattr(
        "connectors.base.get_session",
        lambda organization_id: _FakeSessionContext(integration),
    )
    monkeypatch.setattr(
        "connectors.ispot_tv.httpx.AsyncClient",
        lambda **kwargs: _FakeIspotHttpClient(),
    )

    connector = ISpotTvConnector(
        organization_id=str(uuid4()),
        user_id=str(uuid4()),
    )
    token, _ = await connector.get_oauth_token()
    assert token == "mock-token-123"

    accounts = await connector.sync_accounts()
    assert len(accounts) == 1
    assert accounts[0].source_id == "brand-1"
    assert accounts[0].name == "Test Brand"
    assert accounts[0].source_system == "ispot_tv"


@pytest.mark.asyncio
async def test_ispot_tv_sync_activities(monkeypatch: pytest.MonkeyPatch) -> None:
    """With mocked HTTP and DB: sync_activities returns ActivityRecords."""
    integration = _fake_integration()
    monkeypatch.setattr(
        "connectors.base.get_session",
        lambda organization_id: _FakeSessionContext(integration),
    )
    monkeypatch.setattr(
        "connectors.ispot_tv.httpx.AsyncClient",
        lambda **kwargs: _FakeIspotHttpClient(),
    )

    connector = ISpotTvConnector(
        organization_id=str(uuid4()),
        user_id=str(uuid4()),
    )
    activities = await connector.sync_activities()
    assert len(activities) == 1
    assert activities[0].source_id == "airing-1"
    assert activities[0].type == "tv_airing"
    assert activities[0].subject == "Test Spot"
    assert activities[0].custom_fields is not None
    assert activities[0].custom_fields.get("network") == "ABC"
    assert activities[0].custom_fields.get("est_spend") == 50000


@pytest.mark.asyncio
async def test_ispot_tv_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """With mocked HTTP and DB: query() returns data from fake API."""
    integration = _fake_integration()
    monkeypatch.setattr(
        "connectors.base.get_session",
        lambda organization_id: _FakeSessionContext(integration),
    )
    monkeypatch.setattr(
        "connectors.ispot_tv.httpx.AsyncClient",
        lambda **kwargs: _FakeIspotHttpClient(),
    )

    connector = ISpotTvConnector(
        organization_id=str(uuid4()),
        user_id=str(uuid4()),
    )
    result = await connector.query(
        '{"endpoint": "metrics/tv/airings", "filters": {"start_date": "2025-01-01", "end_date": "2025-01-31"}, "page_size": 100}'
    )
    assert "data" in result
    assert "query" in result
    assert result["query"]["endpoint"] == "metrics/tv/airings"


@pytest.mark.asyncio
async def test_ispot_tv_missing_credentials_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing client_id/client_secret in extra_data raises ValueError."""
    integration = _fake_integration()
    integration.extra_data = {}
    monkeypatch.setattr(
        "connectors.base.get_session",
        lambda organization_id: _FakeSessionContext(integration),
    )

    connector = ISpotTvConnector(organization_id=str(uuid4()), user_id=str(uuid4()))
    connector._integration = integration

    with pytest.raises(ValueError, match="missing client_id or client_secret"):
        await connector._fetch_token()


def test_ispot_tv_meta() -> None:
    """Connector is discoverable and has expected meta."""
    from connectors.registry import discover_connectors

    registry = discover_connectors()
    assert "ispot_tv" in registry
    meta = registry["ispot_tv"].meta
    assert meta.name == "iSpot.tv"
    assert meta.slug == "ispot_tv"
    assert meta.auth_type.value == "custom"
    assert len(meta.auth_fields) == 2
    assert meta.auth_fields[0].name == "client_id"
    assert meta.auth_fields[1].name == "client_secret"

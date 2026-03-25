"""Tests for HubSpotConnector execute_action (run_on_connector)."""

from __future__ import annotations

from typing import Any

import pytest

from connectors.hubspot import HubSpotConnector


def _connector() -> HubSpotConnector:
    return HubSpotConnector(
        "00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
    )


@pytest.mark.asyncio
async def test_execute_action_unknown_raises() -> None:
    c = _connector()
    with pytest.raises(ValueError, match="Unknown HubSpot action"):
        await c.execute_action("not_a_real_action", {})


@pytest.mark.asyncio
async def test_execute_action_list_marketing_emails(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _connector()
    recorded: list[tuple[str, str, dict[str, Any] | None]] = []

    async def fake_make_request(
        self: HubSpotConnector,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        _max_retries: int = 5,
    ) -> dict[str, Any]:
        recorded.append((method, endpoint, params))
        return {"results": [], "paging": {}}

    monkeypatch.setattr(HubSpotConnector, "_make_request", fake_make_request)
    out = await c.execute_action("list_marketing_emails", {"limit": 7, "after": "cursor1"})
    assert out == {"results": [], "paging": {}}
    assert recorded == [("GET", "/marketing/v3/emails", {"limit": 7, "after": "cursor1"})]


@pytest.mark.asyncio
async def test_execute_action_get_marketing_email(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _connector()
    recorded: list[tuple[str, str, dict[str, Any] | None]] = []

    async def fake_make_request(
        self: HubSpotConnector,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        _max_retries: int = 5,
    ) -> dict[str, Any]:
        recorded.append((method, endpoint, params))
        return {"id": "99", "name": "Newsletter"}

    monkeypatch.setattr(HubSpotConnector, "_make_request", fake_make_request)
    out = await c.execute_action("get_marketing_email", {"id": "  99  "})
    assert out["id"] == "99"
    assert recorded == [("GET", "/marketing/v3/emails/99", None)]


@pytest.mark.asyncio
async def test_execute_action_get_marketing_email_requires_id() -> None:
    c = _connector()
    with pytest.raises(ValueError, match="requires params.id"):
        await c.execute_action("get_marketing_email", {"id": ""})


@pytest.mark.asyncio
async def test_execute_action_list_email_events_filters_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _connector()
    recorded: list[tuple[str, str, dict[str, Any] | None]] = []

    async def fake_make_request(
        self: HubSpotConnector,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        _max_retries: int = 5,
    ) -> dict[str, Any]:
        recorded.append((method, endpoint, params))
        return {"events": [], "hasMore": False, "offset": ""}

    monkeypatch.setattr(HubSpotConnector, "_make_request", fake_make_request)
    await c.execute_action(
        "list_email_events",
        {
            "recipient": "x@y.com",
            "limit": 25,
            "ignored_key": "drop",
        },
    )
    assert recorded == [
        (
            "GET",
            "/email/public/v1/events",
            {"recipient": "x@y.com", "limit": 25},
        )
    ]


@pytest.mark.asyncio
async def test_execute_action_get_contact_default_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _connector()
    recorded: list[tuple[str, str, dict[str, Any] | None]] = []

    async def fake_make_request(
        self: HubSpotConnector,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        _max_retries: int = 5,
    ) -> dict[str, Any]:
        recorded.append((method, endpoint, params))
        return {"id": "501", "properties": {}}

    monkeypatch.setattr(HubSpotConnector, "_make_request", fake_make_request)
    await c.execute_action("get_contact", {"id": "501"})
    assert recorded[0][0] == "GET"
    assert recorded[0][1] == "/crm/v3/objects/contacts/501"
    assert "properties" in (recorded[0][2] or {})


@pytest.mark.asyncio
async def test_execute_action_find_contact_by_email_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _connector()

    async def fake_make_request(
        self: HubSpotConnector,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        _max_retries: int = 5,
    ) -> dict[str, Any]:
        return {"results": []}

    monkeypatch.setattr(HubSpotConnector, "_make_request", fake_make_request)
    out = await c.execute_action("find_contact_by_email", {"email": "nobody@example.com"})
    assert out == {"found": False, "contact": None}

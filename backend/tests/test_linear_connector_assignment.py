from __future__ import annotations

from typing import Any

import pytest

from connectors.linear import LinearConnector


@pytest.mark.asyncio
async def test_resolve_assignee_by_name_matches_email(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = LinearConnector(organization_id="00000000-0000-0000-0000-000000000001")

    async def _fake_list_users() -> list[dict[str, Any]]:
        return [
            {"id": "u1", "name": "Alex Kim", "email": "alex@example.com"},
            {"id": "u2", "name": "Sam Lee", "email": "sam@example.com"},
        ]

    monkeypatch.setattr(connector, "list_users", _fake_list_users)

    user = await connector.resolve_assignee_by_name("sam@example.com")

    assert user is not None
    assert user["id"] == "u2"


@pytest.mark.asyncio
async def test_resolve_assignee_by_name_matches_unique_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = LinearConnector(organization_id="00000000-0000-0000-0000-000000000001")

    async def _fake_list_users() -> list[dict[str, Any]]:
        return [
            {"id": "u1", "name": "Alex Kim", "email": "alex@example.com"},
            {"id": "u2", "name": "Sam Lee", "email": "sam@example.com"},
        ]

    monkeypatch.setattr(connector, "list_users", _fake_list_users)

    user = await connector.resolve_assignee_by_name("alex")

    assert user is not None
    assert user["id"] == "u1"


@pytest.mark.asyncio
async def test_resolve_assignee_by_name_does_not_pick_ambiguous_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = LinearConnector(organization_id="00000000-0000-0000-0000-000000000001")

    async def _fake_list_users() -> list[dict[str, Any]]:
        return [
            {"id": "u1", "name": "Alex Kim", "email": "alex@example.com"},
            {"id": "u2", "name": "Alex Chen", "email": "alex.chen@example.com"},
        ]

    monkeypatch.setattr(connector, "list_users", _fake_list_users)

    user = await connector.resolve_assignee_by_name("alex")

    assert user is None

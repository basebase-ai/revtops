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


@pytest.mark.asyncio
async def test_resolve_assignee_by_name_me_tries_all_identity_forms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = LinearConnector(
        organization_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000010",
    )

    async def _fake_list_users() -> list[dict[str, Any]]:
        return [
            {"id": "lin_123", "name": "Sam Lee", "email": "sam@example.com"},
        ]

    async def _fake_candidates() -> list[str]:
        return ["no-match", "sam@example.com", "lin_123", "Sam Lee"]

    monkeypatch.setattr(connector, "list_users", _fake_list_users)
    monkeypatch.setattr(connector, "_resolve_current_user_assignee_candidates", _fake_candidates)

    user = await connector.resolve_assignee_by_name("me")

    assert user is not None
    assert user["id"] == "lin_123"


@pytest.mark.asyncio
async def test_resolve_state_by_name_maps_todo_alias_to_unstarted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = LinearConnector(organization_id="00000000-0000-0000-0000-000000000001")

    async def _fake_workflow_states(team_id: str) -> list[dict[str, Any]]:
        assert team_id == "team_1"
        return [
            {"id": "s1", "name": "Backlog", "type": "backlog", "position": 0},
            {"id": "s2", "name": "Todo", "type": "unstarted", "position": 1},
            {"id": "s3", "name": "In Progress", "type": "started", "position": 2},
        ]

    monkeypatch.setattr(connector, "list_workflow_states", _fake_workflow_states)

    state = await connector.resolve_state_by_name("team_1", "to do")

    assert state is not None
    assert state["id"] == "s2"

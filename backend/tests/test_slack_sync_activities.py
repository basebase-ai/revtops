from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from connectors.slack import SlackConnector


ORG_ID = "00000000-0000-0000-0000-000000000001"


def _make_connector() -> SlackConnector:
    connector = SlackConnector(organization_id=ORG_ID)
    connector._integration = SimpleNamespace(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        user_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        connected_by_user_id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        share_synced_data=False,
        last_sync_at=None,
    )
    return connector


def test_ensure_channel_membership_joins_public_non_member_channels(monkeypatch) -> None:
    connector = _make_connector()
    joined_ids: list[str] = []

    async def _fake_get_channels() -> list[dict[str, object]]:
        return [
            {
                "id": "C1",
                "name": "general",
                "is_private": False,
                "is_archived": False,
                "is_member": False,
            },
            {
                "id": "C2",
                "name": "random",
                "is_private": False,
                "is_archived": False,
                "is_member": True,
            },
            {
                "id": "G1",
                "name": "secret",
                "is_private": True,
                "is_archived": False,
                "is_member": False,
            },
            {
                "id": "C3",
                "name": "old",
                "is_private": False,
                "is_archived": True,
                "is_member": False,
            },
        ]

    async def _fake_join_channel(channel_id: str) -> bool:
        joined_ids.append(channel_id)
        return True

    async def _fake_broadcast_sync_progress(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(connector, "get_channels", _fake_get_channels)
    monkeypatch.setattr(connector, "join_channel", _fake_join_channel)
    monkeypatch.setattr("connectors.slack.broadcast_sync_progress", _fake_broadcast_sync_progress)

    stats = asyncio.run(connector.ensure_channel_membership())

    assert joined_ids == ["C1"]
    assert stats["joined"] == 1
    assert stats["already_member"] == 1
    assert stats["skipped_private"] == 1
    assert stats["skipped_archived"] == 1
    assert stats["total_listed"] == 4


def test_sync_activities_returns_joined_count(monkeypatch) -> None:
    connector = _make_connector()

    async def _fake_ensure() -> dict[str, int]:
        return {"joined": 3, "already_member": 10, "skipped_private": 2, "skipped_archived": 1, "total_listed": 16}

    async def _fake_broadcast_sync_progress(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(connector, "ensure_channel_membership", _fake_ensure)
    monkeypatch.setattr("connectors.slack.broadcast_sync_progress", _fake_broadcast_sync_progress)

    count = asyncio.run(connector.sync_activities())
    assert count == 3

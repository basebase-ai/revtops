from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from connectors.slack import SlackConnector


ORG_ID = "00000000-0000-0000-0000-000000000001"
OWNER_USER_ID = "11111111-1111-1111-1111-111111111111"


class _FakeSession:
    def __init__(self) -> None:
        self.statements: list[object] = []
        self.commit_calls = 0
        self.rollback_calls = 0

    async def execute(self, stmt: object) -> None:
        self.statements.append(stmt)

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


class _FakeSessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _make_connector() -> SlackConnector:
    connector = SlackConnector(organization_id=ORG_ID)
    connector._integration = SimpleNamespace(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        user_id=uuid.UUID(OWNER_USER_ID),
        connected_by_user_id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        share_synced_data=False,
        last_sync_at=None,
    )
    return connector


def test_sync_activities_passes_owner_user_id_into_rls_session(monkeypatch) -> None:
    connector = _make_connector()
    fake_session = _FakeSession()
    captured: dict[str, str | None] = {}

    def _fake_get_session(*, organization_id: str | None = None, user_id: str | None = None):
        captured["organization_id"] = organization_id
        captured["user_id"] = user_id
        return _FakeSessionContext(fake_session)

    async def _fake_get_channels() -> list[dict[str, object]]:
        return [
            {
                "id": "C123",
                "name": "general",
                "is_private": False,
                "is_member": True,
                "num_members": 1,
                "updated": datetime.utcnow().timestamp(),
            }
        ]

    async def _fake_get_channel_messages(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "ts": "1710000000.000100",
                "text": "hello",
                "user": "U123",
                "user_profile": {"display_name": "Test User"},
            }
        ]

    async def _fake_broadcast_sync_progress(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr("connectors.slack.get_session", _fake_get_session)
    monkeypatch.setattr(connector, "get_channels", _fake_get_channels)
    monkeypatch.setattr(connector, "get_channel_messages", _fake_get_channel_messages)
    monkeypatch.setattr("connectors.slack.broadcast_sync_progress", _fake_broadcast_sync_progress)

    count, channels_with_messages = asyncio.run(connector.sync_activities())

    assert count == 1
    assert channels_with_messages == 1
    assert captured == {"organization_id": ORG_ID, "user_id": OWNER_USER_ID}


def test_sync_activities_upsert_includes_visibility_fields(monkeypatch) -> None:
    connector = _make_connector()
    fake_session = _FakeSession()

    def _fake_get_session(*, organization_id: str | None = None, user_id: str | None = None):
        return _FakeSessionContext(fake_session)

    async def _fake_get_channels() -> list[dict[str, object]]:
        return [
            {
                "id": "C123",
                "name": "general",
                "is_private": False,
                "is_member": True,
                "num_members": 1,
                "updated": datetime.utcnow().timestamp(),
            }
        ]

    async def _fake_get_channel_messages(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "ts": "1710000000.000100",
                "text": "hello",
                "user": "U123",
                "user_profile": {"display_name": "Test User"},
            }
        ]

    async def _fake_broadcast_sync_progress(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr("connectors.slack.get_session", _fake_get_session)
    monkeypatch.setattr(connector, "get_channels", _fake_get_channels)
    monkeypatch.setattr(connector, "get_channel_messages", _fake_get_channel_messages)
    monkeypatch.setattr("connectors.slack.broadcast_sync_progress", _fake_broadcast_sync_progress)

    count, channels_with_messages = asyncio.run(connector.sync_activities())

    assert count == 1
    assert channels_with_messages == 1
    assert fake_session.statements, "expected an activity upsert"

    compiled = fake_session.statements[0].compile(dialect=postgresql.dialect())
    params = compiled.params

    assert params["integration_id"] == connector._integration.id
    assert params["owner_user_id"] == connector._integration.user_id
    assert params["visibility"] == "owner_only"
    assert isinstance(params["activity_date"], datetime)

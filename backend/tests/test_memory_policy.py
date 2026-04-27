import asyncio
import sys
import types
from datetime import datetime
from uuid import UUID

from api.routes import memories as memories_api

fake_websockets = types.ModuleType("api.websockets")


async def _broadcast_sync_progress(*_args: object, **_kwargs: object) -> None:
    return None


fake_websockets.broadcast_sync_progress = _broadcast_sync_progress
sys.modules.setdefault("api.websockets", fake_websockets)

from agents.tools import (
    ORG_LEVEL_MEMORY_ERROR,
    _save_memory,
    _validate_memory_entity_type,
    execute_save_memory,
)


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commit_count = 0
        self.refresh_count = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commit_count += 1

    async def refresh(self, obj: object) -> None:
        self.refresh_count += 1
        if getattr(obj, "id", None) is None:
            obj.id = UUID("00000000-0000-0000-0000-0000000000aa")
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime(2026, 1, 1, 0, 0, 0)
        if getattr(obj, "updated_at", None) is None:
            obj.updated_at = datetime(2026, 1, 1, 0, 0, 0)


class _TrackedSessionContext:
    def __init__(self, session: _FakeSession, tracker: dict[str, int]) -> None:
        self.session = session
        self.tracker = tracker

    async def __aenter__(self) -> _FakeSession:
        self.tracker["entered"] += 1
        return self.session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.tracker["exited"] += 1


def test_validate_memory_entity_type_rejects_org_scope() -> None:
    assert _validate_memory_entity_type("organization") == ORG_LEVEL_MEMORY_ERROR


def test_save_memory_rejects_org_scope_with_coherent_error() -> None:
    result = asyncio.run(
        _save_memory(
            params={
                "content": "Remember this for everyone in the org",
                "entity_type": "organization",
            },
            organization_id="00000000-0000-0000-0000-000000000010",
            user_id="00000000-0000-0000-0000-000000000001",
            skip_approval=False,
        )
    )

    assert result == {"error": ORG_LEVEL_MEMORY_ERROR}


def test_execute_save_memory_rejects_org_scope_before_db_work() -> None:
    result = asyncio.run(
        execute_save_memory(
            params={
                "content": "Remember this for everyone in the org",
                "entity_type": "organization",
            },
            organization_id="00000000-0000-0000-0000-000000000010",
            user_id="00000000-0000-0000-0000-000000000001",
        )
    )

    assert result == {
        "status": "failed",
        "error": ORG_LEVEL_MEMORY_ERROR,
    }


def test_create_user_memory_commits_and_exits_session_on_create(monkeypatch) -> None:
    fake_session = _FakeSession()
    tracker = {"entered": 0, "exited": 0}

    def _fake_get_session(*_args: object, **_kwargs: object) -> _TrackedSessionContext:
        return _TrackedSessionContext(fake_session, tracker)

    monkeypatch.setattr(memories_api, "get_session", _fake_get_session)

    response = asyncio.run(
        memories_api.create_user_memory(
            organization_id="00000000-0000-0000-0000-000000000010",
            user_id="00000000-0000-0000-0000-000000000001",
            request=memories_api.CreateMemoryRequest(
                content="  Save this preference  ",
                category="Global_Command",
            ),
        )
    )

    assert tracker == {"entered": 1, "exited": 1}
    assert fake_session.commit_count == 1
    assert fake_session.refresh_count == 1
    assert len(fake_session.added) == 1
    assert response.content == "Save this preference"
    assert response.category == "global_commands"


def test_execute_save_memory_commits_and_exits_session_on_save(monkeypatch) -> None:
    fake_session = _FakeSession()
    tracker = {"entered": 0, "exited": 0}

    def _fake_get_session(*_args: object, **_kwargs: object) -> _TrackedSessionContext:
        return _TrackedSessionContext(fake_session, tracker)

    monkeypatch.setattr("agents.tools.get_session", _fake_get_session)

    result = asyncio.run(
        execute_save_memory(
            params={
                "content": "  Remember my timezone is UTC  ",
                "entity_type": "user",
                "category": "Global_Command",
            },
            organization_id="00000000-0000-0000-0000-000000000010",
            user_id="00000000-0000-0000-0000-000000000001",
        )
    )

    assert tracker == {"entered": 1, "exited": 1}
    assert fake_session.commit_count == 1
    assert len(fake_session.added) == 1
    saved_memory = fake_session.added[0]
    assert saved_memory.content == "Remember my timezone is UTC"
    assert saved_memory.category == "global_commands"
    assert result["status"] == "saved"


def test_global_command_limit_allows_1000_chars() -> None:
    long_content = "x" * 1000
    memories_api.validate_memory_content(long_content, memories_api.GLOBAL_COMMAND_CATEGORY)


def test_channel_scope_normalization_for_slack_thread_id() -> None:
    normalized = memories_api.normalize_channel_scope_channel_id("slack", "C12345678:1714691329.001200")
    assert normalized == "C12345678"

import sys
import types

_fake_websockets = types.ModuleType("api.websockets")

async def _noop_broadcast_sync_progress(*_args: object, **_kwargs: object) -> None:
    return None

_fake_websockets.broadcast_sync_progress = _noop_broadcast_sync_progress
sys.modules.setdefault("api.websockets", _fake_websockets)

import asyncio

from agents.tools import _run_sql_write


ORG_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"


def test_workflow_cannot_create_child_workflow_that_runs_automatically() -> None:
    query = (
        "INSERT INTO workflows (name, trigger_type, is_enabled) "
        "VALUES ('Child workflow', 'schedule', true)"
    )

    result = asyncio.run(
        _run_sql_write(
            params={"query": query},
            organization_id=ORG_ID,
            user_id=USER_ID,
            context={"is_workflow": True, "workflow_id": "wf-parent"},
        )
    )

    assert "error" in result
    assert "cannot create enabled schedule/event workflows" in result["error"]


def test_workflow_cannot_update_child_workflow_to_run_automatically() -> None:
    query = "UPDATE workflows SET is_enabled = true, trigger_type = 'event' WHERE id = 'wf-child'"

    result = asyncio.run(
        _run_sql_write(
            params={"query": query},
            organization_id=ORG_ID,
            user_id=USER_ID,
            context={"is_workflow": True, "workflow_id": "wf-parent"},
        )
    )

    assert "error" in result
    assert "cannot enable or configure schedule/event triggers" in result["error"]


def test_sql_write_passes_user_id_to_session(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeResult:
        rowcount = 1

    class _FakeSession:
        async def execute(self, _query: object) -> _FakeResult:
            return _FakeResult()

        async def commit(self) -> None:
            return None

    class _FakeSessionCtx:
        async def __aenter__(self) -> _FakeSession:
            return _FakeSession()

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    def _fake_get_session(*, organization_id: str, user_id: str | None = None) -> _FakeSessionCtx:
        captured["organization_id"] = organization_id
        captured["user_id"] = user_id
        return _FakeSessionCtx()

    monkeypatch.setattr("agents.tools.get_session", _fake_get_session)

    result = asyncio.run(
        _run_sql_write(
            params={"query": "UPDATE org_members SET title = 'CTO' WHERE id = '8ab46e6b-93a7-424a-898e-ff8bac468756'"},
            organization_id=ORG_ID,
            user_id=USER_ID,
            context=None,
        )
    )

    assert result.get("success") is True
    assert captured == {"organization_id": ORG_ID, "user_id": USER_ID}

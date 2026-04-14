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

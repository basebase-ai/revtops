import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

from workers.tasks import workflows


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeAdminSession:
    def __init__(self, creator_user_id):
        self.creator_user_id = creator_user_id

    async def execute(self, _query):
        return _ScalarResult(self.creator_user_id)



def test_resolve_execution_user_uses_trigger_user_when_valid() -> None:
    trigger_user_id = "00000000-0000-0000-0000-000000000123"

    resolved = asyncio.run(
        workflows._resolve_workflow_execution_user_id(
            workflow_id="00000000-0000-0000-0000-000000000999",
            triggered_by_user_id=trigger_user_id,
        )
    )

    assert resolved == trigger_user_id


def test_resolve_execution_user_falls_back_to_workflow_creator(monkeypatch) -> None:
    creator_user_id = UUID("00000000-0000-0000-0000-000000000456")

    @asynccontextmanager
    async def _fake_admin_session():
        yield _FakeAdminSession(creator_user_id)

    monkeypatch.setattr("models.database.get_admin_session", _fake_admin_session)

    resolved = asyncio.run(
        workflows._resolve_workflow_execution_user_id(
            workflow_id="00000000-0000-0000-0000-000000000999",
            triggered_by_user_id=None,
        )
    )

    assert resolved == str(creator_user_id)

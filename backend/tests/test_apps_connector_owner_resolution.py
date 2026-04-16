import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

from connectors.apps import AppsConnector


class _FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, *, message_user_id: UUID | None, conversation_user_id: UUID | None):
        self.message_user_id = message_user_id
        self.conversation_user_id = conversation_user_id
        self.added = []
        self.execute_calls = 0
        self.committed = False

    async def execute(self, _query, _params=None):
        self.execute_calls += 1
        if self.execute_calls == 1:
            return _FakeExecuteResult(self.message_user_id)
        return _FakeExecuteResult(self.conversation_user_id)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def test_create_prefers_turn_user_over_conversation_owner(monkeypatch):
    org_id = "00000000-0000-0000-0000-000000000010"
    turn_user_id = "00000000-0000-0000-0000-000000000011"
    message_user_id = UUID("00000000-0000-0000-0000-000000000012")
    conversation_user_id = UUID("00000000-0000-0000-0000-000000000013")
    fake_session = _FakeSession(
        message_user_id=message_user_id,
        conversation_user_id=conversation_user_id,
    )

    @asynccontextmanager
    async def _fake_get_session(*_args, **_kwargs):
        yield fake_session

    async def _fake_warm(*_args, **_kwargs):
        return None

    monkeypatch.setattr("connectors.apps.get_session", _fake_get_session)
    monkeypatch.setattr("connectors.apps.warm_public_preview_cache", _fake_warm)
    monkeypatch.setattr("utils.transpile_jsx.transpile_jsx", lambda _code: (None,))

    connector = AppsConnector(organization_id=org_id, user_id=turn_user_id)

    result = asyncio.run(
        connector._create(
            {
                "title": "Slack-owned app",
                "queries": {
                    "q": {"sql": "SELECT 1 AS n", "params": {}},
                },
                "frontend_code": "export default function App(){ return <div/>; }",
                "message_id": "00000000-0000-0000-0000-000000000014",
                "conversation_id": "00000000-0000-0000-0000-000000000015",
            }
        )
    )

    assert result["status"] == "success"
    assert fake_session.committed is True
    assert len(fake_session.added) == 1
    assert fake_session.added[0].user_id == UUID(turn_user_id)

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

from api.auth_middleware import AuthContext
from api.routes import artifacts


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _FakeArtifactsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _FakeScalars(self._values)


class _FakeSession:
    def __init__(self, conv_results, artifacts_rows):
        self._conv_results = list(conv_results)
        self._artifacts_rows = artifacts_rows

    async def execute(self, query):
        query_text = str(query)
        if "FROM conversations" in query_text:
            return _FakeScalarResult(self._conv_results.pop(0))
        return _FakeArtifactsResult(self._artifacts_rows)



def _auth() -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        organization_id=uuid4(),
        email="user@example.com",
        role="member",
        is_global_admin=False,
    )


def test_list_conversation_artifacts_retries_with_slack_user_ids(monkeypatch):
    conv_id = uuid4()
    fake_conv = SimpleNamespace(id=conv_id)
    fake_artifact = SimpleNamespace(
        id=uuid4(),
        type="note",
        title="A",
        description=None,
        content_type="text",
        mime_type="text/plain",
        filename="a.txt",
        conversation_id=conv_id,
        message_id=None,
        created_at=None,
        user_id=None,
        visibility="team",
    )
    fake_session = _FakeSession(conv_results=[None, fake_conv], artifacts_rows=[fake_artifact])

    @asynccontextmanager
    async def _fake_get_session(*_args, **_kwargs):
        yield fake_session

    async def _fake_slack_ids(*_args, **_kwargs):
        return {"U123"}

    monkeypatch.setattr(artifacts, "get_session", _fake_get_session)
    monkeypatch.setattr(artifacts, "_get_slack_user_ids", _fake_slack_ids)

    response = asyncio.run(artifacts.list_conversation_artifacts(str(conv_id), auth=_auth()))

    assert response.total == 1
    assert response.artifacts[0].conversation_id == str(conv_id)

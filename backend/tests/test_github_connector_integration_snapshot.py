import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from connectors.github import GitHubConnector


class _FakeExecuteResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self._row = row

    async def execute(self, _query):
        return _FakeExecuteResult(self._row)


class _FakeSessionContext:
    def __init__(self, row):
        self._session = _FakeSession(row)

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeNango:
    def __init__(self):
        self.calls = []

    async def get_token(self, integration_id, connection_id):
        self.calls.append((integration_id, connection_id))
        return "gh-token"


def test_get_oauth_token_uses_scalar_snapshot(monkeypatch):
    last_sync_at = datetime.utcnow() - timedelta(hours=1)
    row = SimpleNamespace(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        nango_connection_id="conn-gh-123",
        last_sync_at=last_sync_at,
    )

    monkeypatch.setattr(
        "connectors.github.get_session",
        lambda organization_id: _FakeSessionContext(row),
    )
    fake_nango = _FakeNango()
    monkeypatch.setattr("connectors.github.get_nango_client", lambda: fake_nango)

    connector = GitHubConnector(
        organization_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
    )
    token, _ = asyncio.run(connector.get_oauth_token())

    assert token == "gh-token"
    assert fake_nango.calls == [("github", "conn-gh-123")]
    assert connector.sync_since == last_sync_at - connector._SYNC_SINCE_BUFFER

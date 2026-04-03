import asyncio
from types import SimpleNamespace

from connectors.base import BaseConnector


class _DummyConnector(BaseConnector):
    source_system = "slack"

    async def sync_deals(self) -> int:
        return 0

    async def sync_accounts(self) -> int:
        return 0

    async def sync_contacts(self) -> int:
        return 0

    async def sync_activities(self) -> int:
        return 0

    async def fetch_deal(self, deal_id: str) -> dict:
        return {"id": deal_id}


class _FakeScalarCollection:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarCollection(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.expunge_calls = []

    async def execute(self, _query):
        return _FakeExecuteResult(self._rows)

    def expunge(self, row):
        self.expunge_calls.append(row)
        if hasattr(row, "_expunged"):
            row._expunged = True


class _FakeSessionContext:
    def __init__(self, rows):
        self._session = _FakeSession(rows)

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeNango:
    def __init__(self):
        self.calls = []

    async def get_token(self, integration_id, connection_id):
        self.calls.append((integration_id, connection_id))
        return "token-123"


class _ExpungeRequiredIntegration:
    def __init__(self, integration_id: str, connection_id: str):
        self.id = integration_id
        self._connection_id = connection_id
        self._expunged = False

    @property
    def nango_connection_id(self):
        if not self._expunged:
            raise RuntimeError("simulated detached instance attribute refresh")
        return self._connection_id


def test_get_oauth_token_handles_multiple_integrations_without_user_id(monkeypatch):
    first = SimpleNamespace(id="int-new", nango_connection_id="conn-new")
    second = SimpleNamespace(id="int-old", nango_connection_id="conn-old")

    monkeypatch.setattr(
        "connectors.base.get_session",
        lambda organization_id: _FakeSessionContext([first, second]),
    )

    fake_nango = _FakeNango()
    monkeypatch.setattr("connectors.base.get_nango_client", lambda: fake_nango)
    monkeypatch.setattr("connectors.base.get_nango_integration_id", lambda _source: "nango-slack")

    connector = _DummyConnector(organization_id="11111111-1111-1111-1111-111111111111")
    token, instance = asyncio.run(connector.get_oauth_token())

    assert token == "token-123"
    assert instance == ""
    assert fake_nango.calls == [("nango-slack", "conn-new")]


def test_get_oauth_token_uses_expunged_integration_instance(monkeypatch):
    row = _ExpungeRequiredIntegration("int-new", "conn-new")

    monkeypatch.setattr(
        "connectors.base.get_session",
        lambda organization_id: _FakeSessionContext([row]),
    )

    fake_nango = _FakeNango()
    monkeypatch.setattr("connectors.base.get_nango_client", lambda: fake_nango)
    monkeypatch.setattr("connectors.base.get_nango_integration_id", lambda _source: "nango-slack")

    connector = _DummyConnector(organization_id="11111111-1111-1111-1111-111111111111")
    token, _ = asyncio.run(connector.get_oauth_token())

    assert token == "token-123"
    assert fake_nango.calls == [("nango-slack", "conn-new")]

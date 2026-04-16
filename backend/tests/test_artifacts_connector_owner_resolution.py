import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

from connectors.artifacts import ArtifactConnector


class _FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def one_or_none(self):
        return self._value

    def all(self):
        return self._value


class _FakeSession:
    def __init__(
        self,
        *,
        message_user_id: UUID | None,
        conversation_user_id: UUID | None,
        org_handle: str | None = None,
        conversation_source: str | None = None,
        conversation_source_user_id: str | None = None,
        slack_mapping_user_id: UUID | None = None,
        legacy_mapping_user_id: UUID | None = None,
    ):
        self.message_user_id = message_user_id
        self.conversation_user_id = conversation_user_id
        self.org_handle = org_handle
        self.conversation_source = conversation_source
        self.conversation_source_user_id = conversation_source_user_id
        self.slack_mapping_user_id = slack_mapping_user_id
        self.legacy_mapping_user_id = legacy_mapping_user_id
        self.mapping_query_calls = 0
        self.added = []
        self.committed = False

    async def execute(self, query, _params=None):
        q = str(query)
        if "chat_messages" in q:
            return _FakeExecuteResult(self.message_user_id)
        if "conversations" in q:
            return _FakeExecuteResult(
                (
                    self.conversation_user_id,
                    self.conversation_source,
                    self.conversation_source_user_id,
                )
            )
        if "user_mappings_for_identity" in q:
            self.mapping_query_calls += 1
            if self.mapping_query_calls == 1:
                if self.slack_mapping_user_id is None:
                    return _FakeExecuteResult([])
                return _FakeExecuteResult([
                    (self.conversation_source_user_id, "slack", self.slack_mapping_user_id)
                ])
            if self.legacy_mapping_user_id is None:
                return _FakeExecuteResult([])
            return _FakeExecuteResult([
                (self.conversation_source_user_id, "revtops_unknown", self.legacy_mapping_user_id)
            ])
        if "organizations.handle" in q:
            return _FakeExecuteResult(self.org_handle)
        raise AssertionError(f"Unexpected query: {q}")

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

    async def _fake_alternate(*_args, **_kwargs):
        return []

    monkeypatch.setattr("connectors.artifacts.get_session", _fake_get_session)
    monkeypatch.setattr("connectors.artifacts.warm_public_preview_cache", _fake_warm)
    monkeypatch.setattr("connectors.artifacts.get_alternate_slack_user_ids_for_identity", _fake_alternate)

    connector = ArtifactConnector(organization_id=org_id, user_id=turn_user_id)

    result = asyncio.run(
        connector._create(
            {
                "title": "Slack-owned artifact",
                "filename": "artifact.md",
                "content_type": "markdown",
                "content": "hello",
                "message_id": "00000000-0000-0000-0000-000000000014",
                "conversation_id": "00000000-0000-0000-0000-000000000015",
            }
        )
    )

    assert result["status"] == "success"
    assert "/artifacts/" in result["uri"]
    assert fake_session.committed is True
    assert len(fake_session.added) == 1
    assert fake_session.added[0].user_id == message_user_id



def test_create_resolves_owner_from_slack_identity_mapping_when_conversation_user_missing(monkeypatch):
    org_id = "00000000-0000-0000-0000-000000000010"
    resolved_user_id = UUID("00000000-0000-0000-0000-000000000099")
    fake_session = _FakeSession(
        message_user_id=None,
        conversation_user_id=None,
        conversation_source="slack",
        conversation_source_user_id="U123SLACK",
        slack_mapping_user_id=resolved_user_id,
    )

    @asynccontextmanager
    async def _fake_get_session(*_args, **_kwargs):
        yield fake_session

    async def _fake_warm(*_args, **_kwargs):
        return None

    async def _fake_alternate(*_args, **_kwargs):
        return []

    monkeypatch.setattr("connectors.artifacts.get_session", _fake_get_session)
    monkeypatch.setattr("connectors.artifacts.warm_public_preview_cache", _fake_warm)
    monkeypatch.setattr("connectors.artifacts.get_alternate_slack_user_ids_for_identity", _fake_alternate)

    connector = ArtifactConnector(organization_id=org_id, user_id=None)

    result = asyncio.run(
        connector._create(
            {
                "title": "Slack-owned artifact",
                "filename": "artifact.md",
                "content_type": "markdown",
                "content": "hello",
                "message_id": "00000000-0000-0000-0000-000000000014",
                "conversation_id": "00000000-0000-0000-0000-000000000015",
            }
        )
    )

    assert result["status"] == "success"
    assert "/artifacts/" in result["uri"]
    assert fake_session.committed is True
    assert len(fake_session.added) == 1
    assert fake_session.added[0].user_id == resolved_user_id


def test_create_returns_org_handle_scoped_uri_when_handle_present(monkeypatch):
    org_id = "00000000-0000-0000-0000-000000000010"
    fake_session = _FakeSession(
        message_user_id=UUID("00000000-0000-0000-0000-000000000012"),
        conversation_user_id=None,
        org_handle="acme",
    )

    @asynccontextmanager
    async def _fake_get_session(*_args, **_kwargs):
        yield fake_session

    async def _fake_warm(*_args, **_kwargs):
        return None

    async def _fake_alternate(*_args, **_kwargs):
        return []

    monkeypatch.setattr("connectors.artifacts.get_session", _fake_get_session)
    monkeypatch.setattr("connectors.artifacts.warm_public_preview_cache", _fake_warm)
    monkeypatch.setattr("connectors.artifacts.get_alternate_slack_user_ids_for_identity", _fake_alternate)

    connector = ArtifactConnector(organization_id=org_id, user_id=None)
    result = asyncio.run(
        connector._create(
            {
                "title": "Handle-scoped artifact",
                "filename": "artifact.md",
                "content_type": "markdown",
                "content": "hello",
                "message_id": "00000000-0000-0000-0000-000000000014",
            }
        )
    )

    assert result["status"] == "success"
    assert result["uri"].startswith("/acme/artifacts/")

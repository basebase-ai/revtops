import asyncio
from types import SimpleNamespace
from uuid import UUID

from services import slack_conversations


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)


class _FakeSession:
    def __init__(self, query_results):
        self._query_results = list(query_results)

    async def execute(self, _query):
        return _FakeExecuteResult(self._query_results.pop(0))


class _FakeAdminSessionContext:
    def __init__(self, query_results):
        self._session = _FakeSession(query_results)

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSlackConnector:
    def __init__(self, organization_id: str):
        self.organization_id = organization_id

    async def get_user_info(self, slack_user_id: str):
        assert slack_user_id == "U123"
        return {
            "name": "jane-doe",
            "real_name": "Jane Doe",
            "profile": {
                "email": "",
                "display_name": "Jane Doe",
                "real_name": "Jane Doe",
                "display_name_normalized": "Jane Doe",
                "real_name_normalized": "Jane Doe",
            },
        }


def test_resolve_revtops_user_falls_back_to_connected_slack_name_match(monkeypatch):
    org_id = "11111111-1111-1111-1111-111111111111"
    jane_id = UUID("22222222-2222-2222-2222-222222222222")
    john_id = UUID("33333333-3333-3333-3333-333333333333")

    users = [
        SimpleNamespace(id=john_id, email="john@acme.com", name="John Smith"),
        SimpleNamespace(id=jane_id, email="other@acme.com", name="  Jane   Doe  "),
    ]
    integrations = [
        SimpleNamespace(user_id=jane_id, connected_by_user_id=None),
    ]

    monkeypatch.setattr(
        slack_conversations,
        "get_admin_session",
        lambda: _FakeAdminSessionContext([users, integrations]),
    )
    monkeypatch.setattr(slack_conversations, "SlackConnector", _FakeSlackConnector)

    resolved = asyncio.run(
        slack_conversations.resolve_revtops_user_for_slack_actor(
            organization_id=org_id,
            slack_user_id="U123",
        )
    )

    assert resolved is not None
    assert resolved.id == jane_id

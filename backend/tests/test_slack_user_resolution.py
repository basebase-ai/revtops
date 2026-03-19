"""Tests for Slack identity resolution functions in services.slack_identity."""
import asyncio

from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from services import slack_identity


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

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        return self._rows[0]


class _FakeSession:
    def __init__(self, query_results):
        self._query_results = list(query_results)
        self.commit_calls = 0

    async def execute(self, _query):
        return _FakeExecuteResult(self._query_results.pop(0))

    async def commit(self):
        self.commit_calls += 1


class _FakeAdminSessionContext:
    def __init__(self, query_results):
        self._session = _FakeSession(query_results)

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSlackConnector:
    def __init__(self, organization_id: str, team_id: str | None = None):
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
        SimpleNamespace(user_id=jane_id, connected_by_user_id=None, extra_data={}, id="int-1"),
    ]

    monkeypatch.setattr(
        slack_identity,
        "get_admin_session",
        lambda: _FakeAdminSessionContext([users, integrations, []]),
    )
    monkeypatch.setattr(slack_identity, "SlackConnector", _FakeSlackConnector)

    resolved = asyncio.run(
        slack_identity.resolve_revtops_user_for_slack_actor(
            organization_id=org_id,
            slack_user_id="U123",
        )
    )

    assert resolved is not None
    assert resolved.id == jane_id


def test_resolve_revtops_user_matches_slack_metadata(monkeypatch):
    org_id = "11111111-1111-1111-1111-111111111111"
    jane_id = UUID("22222222-2222-2222-2222-222222222222")

    users = [
        SimpleNamespace(id=jane_id, email="jane@acme.com", name="Jane Doe"),
    ]
    integrations = [
        SimpleNamespace(
            user_id=None,
            connected_by_user_id=jane_id,
            extra_data={"authed_user": {"id": "U456"}},
            id="int-2",
        ),
    ]

    monkeypatch.setattr(
        slack_identity,
        "get_admin_session",
        lambda: _FakeAdminSessionContext([users, integrations, []]),
    )

    class _NoSlackConnector:
        def __init__(self, organization_id: str, team_id: str | None = None):
            raise AssertionError("SlackConnector should not be called when metadata matches")

    monkeypatch.setattr(slack_identity, "SlackConnector", _NoSlackConnector)

    resolved = asyncio.run(
        slack_identity.resolve_revtops_user_for_slack_actor(
            organization_id=org_id,
            slack_user_id="U456",
        )
    )

    assert resolved is not None
    assert resolved.id == jane_id


def test_resolve_revtops_user_uses_legacy_mapping_source_and_normalizes_id(monkeypatch):
    org_id = "11111111-1111-1111-1111-111111111111"
    jane_id = UUID("22222222-2222-2222-2222-222222222222")

    users = [
        SimpleNamespace(id=jane_id, email="jane@acme.com", name="Jane Doe"),
    ]
    integrations = []
    mappings = [
        SimpleNamespace(
            user_id=jane_id,
            external_userid="U888",
            source="revtops_unknown",
            updated_at=None,
        ),
    ]

    monkeypatch.setattr(
        slack_identity,
        "get_admin_session",
        lambda: _FakeAdminSessionContext([users, integrations, mappings]),
    )

    class _NoSlackConnector:
        def __init__(self, organization_id: str, team_id: str | None = None):
            raise AssertionError("SlackConnector should not be called when legacy mapping exists")

    monkeypatch.setattr(slack_identity, "SlackConnector", _NoSlackConnector)

    resolved = asyncio.run(
        slack_identity.resolve_revtops_user_for_slack_actor(
            organization_id=org_id,
            slack_user_id=" u888 ",
        )
    )

    assert resolved is not None
    assert resolved.id == jane_id


def test_resolve_revtops_user_uses_existing_mapping(monkeypatch):
    org_id = "11111111-1111-1111-1111-111111111111"
    jane_id = UUID("22222222-2222-2222-2222-222222222222")

    users = [
        SimpleNamespace(id=jane_id, email="jane@acme.com", name="Jane Doe"),
    ]
    integrations = []
    mappings = [
        SimpleNamespace(user_id=jane_id, external_userid="U999"),
    ]

    monkeypatch.setattr(
        slack_identity,
        "get_admin_session",
        lambda: _FakeAdminSessionContext([users, integrations, mappings]),
    )

    class _NoSlackConnector:
        def __init__(self, organization_id: str, team_id: str | None = None):
            raise AssertionError("SlackConnector should not be called when mapping exists")

    monkeypatch.setattr(slack_identity, "SlackConnector", _NoSlackConnector)

    resolved = asyncio.run(
        slack_identity.resolve_revtops_user_for_slack_actor(
            organization_id=org_id,
            slack_user_id="U999",
        )
    )

    assert resolved is not None
    assert resolved.id == jane_id


def test_resolve_revtops_user_falls_back_to_guest_when_slack_profile_missing(monkeypatch):
    org_id = "11111111-1111-1111-1111-111111111111"
    member_id = UUID("22222222-2222-2222-2222-222222222222")
    guest_id = UUID("33333333-3333-3333-3333-333333333333")
    users = [
        SimpleNamespace(id=member_id, email="member@acme.com", name="Member User"),
    ]

    monkeypatch.setattr(
        slack_identity,
        "get_admin_session",
        lambda: _FakeAdminSessionContext([users, [], []]),
    )

    async def _fake_fetch_slack_user_info(organization_id: str, slack_user_id: str):
        return None

    monkeypatch.setattr(
        slack_identity,
        "_fetch_slack_user_info",
        _fake_fetch_slack_user_info,
    )
    guest_user = SimpleNamespace(id=guest_id, is_guest=True)

    async def _fake_resolve_guest(_organization_id: str):
        return guest_user

    monkeypatch.setattr(slack_identity, "_resolve_guest_user_for_org", _fake_resolve_guest)

    resolved = asyncio.run(
        slack_identity.resolve_revtops_user_for_slack_actor(
            organization_id=org_id,
            slack_user_id="U404",
        )
    )

    assert resolved is not None
    assert resolved.id == guest_id


def test_resolve_revtops_user_falls_back_to_guest_for_empty_slack_id(monkeypatch):
    org_id = "11111111-1111-1111-1111-111111111111"
    guest_id = UUID("33333333-3333-3333-3333-333333333333")
    guest_user = SimpleNamespace(id=guest_id, is_guest=True)

    async def _fake_resolve_guest(_organization_id: str):
        return guest_user

    monkeypatch.setattr(slack_identity, "_resolve_guest_user_for_org", _fake_resolve_guest)

    resolved = asyncio.run(
        slack_identity.resolve_revtops_user_for_slack_actor(
            organization_id=org_id,
            slack_user_id="   ",
        )
    )

    assert resolved is not None
    assert resolved.id == guest_id


def test_get_alternate_slack_user_ids_for_identity_returns_updated_preference_order(monkeypatch):
    org_id = "11111111-1111-1111-1111-111111111111"
    user_id = UUID("22222222-2222-2222-2222-222222222222")
    newer = datetime(2026, 3, 19, 12, 0, 0)
    older = datetime(2026, 3, 19, 11, 0, 0)

    seed_rows = [
        SimpleNamespace(
            user_id=user_id,
            external_userid="U123",
            external_email="person@example.com",
            updated_at=newer,
        ),
    ]
    related_rows = [
        SimpleNamespace(
            user_id=user_id,
            external_userid="U123",
            external_email="person@example.com",
            updated_at=newer,
        ),
        SimpleNamespace(
            user_id=user_id,
            external_userid="U456",
            external_email="person@example.com",
            updated_at=older,
        ),
        SimpleNamespace(
            user_id=user_id,
            external_userid="U789",
            external_email="person@example.com",
            updated_at=datetime(2026, 3, 19, 10, 0, 0),
        ),
    ]

    monkeypatch.setattr(
        slack_identity,
        "get_admin_session",
        lambda: _FakeAdminSessionContext([seed_rows, related_rows]),
    )

    alternate_ids = asyncio.run(
        slack_identity.get_alternate_slack_user_ids_for_identity(
            organization_id=org_id,
            slack_user_id="u123",
        )
    )

    assert alternate_ids == ["U456", "U789"]


def test_demote_slack_user_id_preference_moves_user_not_found_to_bottom(monkeypatch):
    org_id = "11111111-1111-1111-1111-111111111111"
    user_id = UUID("22222222-2222-2222-2222-222222222222")
    target = SimpleNamespace(
        user_id=user_id,
        external_userid="U123",
        external_email="person@example.com",
        created_at=datetime(2026, 3, 19, 9, 0, 0),
        updated_at=datetime(2026, 3, 19, 12, 0, 0),
    )
    sibling = SimpleNamespace(
        user_id=user_id,
        external_userid="U456",
        external_email="person@example.com",
        created_at=datetime(2026, 3, 19, 8, 0, 0),
        updated_at=datetime(2026, 3, 19, 11, 0, 0),
    )
    fake_ctx = _FakeAdminSessionContext([[target], [target, sibling]])

    monkeypatch.setattr(slack_identity, "get_admin_session", lambda: fake_ctx)

    asyncio.run(
        slack_identity.demote_slack_user_id_preference(
            organization_id=org_id,
            slack_user_id="U123",
        )
    )

    assert fake_ctx._session.commit_calls == 1
    assert target.updated_at < sibling.updated_at

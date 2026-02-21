import asyncio
from types import SimpleNamespace
from uuid import UUID

from api.routes import auth


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, target_user, mapping, related_rows):
        self.target_user = target_user
        self.mapping = mapping
        self.related_rows = related_rows
        self.execute_calls = 0
        self.committed = False

    async def get(self, _model, model_id):
        if model_id == self.target_user.id:
            return self.target_user
        if model_id == self.mapping.id:
            return self.mapping
        return None

    async def execute(self, _query):
        self.execute_calls += 1
        return _FakeExecuteResult(self.related_rows)

    async def commit(self):
        self.committed = True


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_link_identity_links_related_slack_mappings(monkeypatch):
    org_id = UUID("11111111-1111-1111-1111-111111111111")
    requester_id = UUID("22222222-2222-2222-2222-222222222222")
    target_user_id = UUID("33333333-3333-3333-3333-333333333333")
    selected_mapping_id = UUID("44444444-4444-4444-4444-444444444444")
    related_mapping_id = UUID("55555555-5555-5555-5555-555555555555")

    target_user = SimpleNamespace(id=target_user_id, organization_id=org_id, email="owner@acme.com")
    selected_mapping = SimpleNamespace(
        id=selected_mapping_id,
        organization_id=org_id,
        source="slack",
        external_userid="U123",
        external_email=None,
        user_id=None,
        revtops_email=None,
        match_source="unmatched",
    )
    related_mapping = SimpleNamespace(
        id=related_mapping_id,
        organization_id=org_id,
        source="slack",
        external_userid=None,
        external_email="owner@acme.com",
        user_id=None,
        revtops_email=None,
        match_source="unmatched",
    )

    fake_session = _FakeSession(target_user, selected_mapping, [related_mapping])
    monkeypatch.setattr(auth, "get_session", lambda: _FakeSessionContext(fake_session))

    result = asyncio.run(
        auth.link_identity(
            org_id=str(org_id),
            request=auth.LinkIdentityRequest(
                target_user_id=str(target_user_id),
                mapping_id=str(selected_mapping_id),
            ),
            user_id=str(requester_id),
        )
    )

    assert result == {"status": "linked"}
    assert selected_mapping.user_id == target_user_id
    assert selected_mapping.external_email == "owner@acme.com"
    assert selected_mapping.revtops_email == "owner@acme.com"
    assert selected_mapping.match_source == "admin_manual_link"

    assert related_mapping.user_id == target_user_id
    assert related_mapping.revtops_email == "owner@acme.com"
    assert related_mapping.match_source == "admin_manual_link"
    assert fake_session.execute_calls == 1
    assert fake_session.committed


def test_link_identity_non_slack_does_not_attempt_related_linking(monkeypatch):
    org_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    requester_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    target_user_id = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    selected_mapping_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")

    target_user = SimpleNamespace(id=target_user_id, organization_id=org_id, email="owner@acme.com")
    selected_mapping = SimpleNamespace(
        id=selected_mapping_id,
        organization_id=org_id,
        source="hubspot",
        external_userid="HS-1",
        external_email=None,
        user_id=None,
        revtops_email=None,
        match_source="unmatched",
    )

    fake_session = _FakeSession(target_user, selected_mapping, [])
    monkeypatch.setattr(auth, "get_session", lambda: _FakeSessionContext(fake_session))

    result = asyncio.run(
        auth.link_identity(
            org_id=str(org_id),
            request=auth.LinkIdentityRequest(
                target_user_id=str(target_user_id),
                mapping_id=str(selected_mapping_id),
            ),
            user_id=str(requester_id),
        )
    )

    assert result == {"status": "linked"}
    assert selected_mapping.user_id == target_user_id
    assert selected_mapping.match_source == "admin_manual_link"
    assert fake_session.execute_calls == 0
    assert fake_session.committed

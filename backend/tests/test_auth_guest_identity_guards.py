import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from api.routes import auth


class _FakeMembershipResult:
    def scalar_one_or_none(self) -> object:
        return True  # Simulate active org membership

class _FakeSession:
    def __init__(self, *, users, mapping):
        self._users = users
        self._mapping = mapping
        self.committed = False

    async def get(self, _model, model_id):
        if model_id == self._mapping.id:
            return self._mapping
        return self._users.get(model_id)

    async def execute(self, _query, _params=None):
        return _FakeMembershipResult()

    async def commit(self):
        self.committed = True


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_link_identity_rejects_guest_target(monkeypatch):
    org_id = UUID("11111111-1111-1111-1111-111111111111")
    requester_id = UUID("22222222-2222-2222-2222-222222222222")
    target_user_id = UUID("33333333-3333-3333-3333-333333333333")
    mapping_id = UUID("44444444-4444-4444-4444-444444444444")

    guest_user = SimpleNamespace(id=target_user_id, organization_id=org_id, email="g@x", is_guest=True)
    mapping = SimpleNamespace(
        id=mapping_id,
        organization_id=org_id,
        source="slack",
        external_userid="U123",
        external_email=None,
        user_id=None,
        revtops_email=None,
        match_source="unmatched",
    )

    fake_session = _FakeSession(users={target_user_id: guest_user}, mapping=mapping)
    monkeypatch.setattr(auth, "get_session", lambda **kwargs: _FakeSessionContext(fake_session))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            auth.link_identity(
                org_id=str(org_id),
                request=auth.LinkIdentityRequest(target_user_id=str(target_user_id), mapping_id=str(mapping_id)),
                user_id=str(requester_id),
            )
        )

    assert exc.value.status_code == 403
    assert not fake_session.committed


def test_unlink_identity_rejects_guest_mapping(monkeypatch):
    org_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    requester_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    guest_user_id = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    mapping_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")

    requester = SimpleNamespace(id=requester_id, organization_id=org_id, is_guest=False)
    guest_user = SimpleNamespace(id=guest_user_id, organization_id=org_id, is_guest=True)
    mapping = SimpleNamespace(
        id=mapping_id,
        organization_id=org_id,
        user_id=guest_user_id,
        revtops_email="guest@example.com",
        match_source="auto",
    )

    fake_session = _FakeSession(users={requester_id: requester, guest_user_id: guest_user}, mapping=mapping)
    monkeypatch.setattr(auth, "get_session", lambda **kwargs: _FakeSessionContext(fake_session))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            auth.unlink_identity(
                org_id=str(org_id),
                request=auth.UnlinkIdentityRequest(mapping_id=str(mapping_id)),
                user_id=str(requester_id),
            )
        )

    assert exc.value.status_code == 403
    assert mapping.user_id == guest_user_id
    assert not fake_session.committed

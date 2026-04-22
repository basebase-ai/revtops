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


def test_update_guest_user_scopes_session_to_org(monkeypatch):
    org_id = UUID("12121212-1212-1212-1212-121212121212")
    requester_id = UUID("34343434-3434-3434-3434-343434343434")
    guest_user_id = UUID("56565656-5656-5656-5656-565656565656")
    captured: dict[str, str] = {}

    requester = SimpleNamespace(id=requester_id, is_guest=False)
    org = SimpleNamespace(id=org_id, guest_user_id=guest_user_id, guest_user_enabled=False)
    guest_user = SimpleNamespace(id=guest_user_id, guest_organization_id=org_id, is_guest=True)

    class _GuestToggleSession:
        def __init__(self):
            self.committed = False

        async def get(self, model, model_id):
            if model is auth.User:
                if model_id == requester_id:
                    return requester
                if model_id == guest_user_id:
                    return guest_user
            if model is auth.Organization and model_id == org_id:
                return org
            return None

        async def commit(self):
            self.committed = True

    guest_toggle_session = _GuestToggleSession()

    def _fake_get_session(**kwargs):
        captured["organization_id"] = kwargs.get("organization_id")
        return _FakeSessionContext(guest_toggle_session)

    async def _allow_admin(_session, _user, _org_uuid):
        return True

    monkeypatch.setattr(auth, "get_session", _fake_get_session)
    monkeypatch.setattr(auth, "_can_administer_org", _allow_admin)

    result = asyncio.run(
        auth.update_guest_user(
            org_id=str(org_id),
            request=auth.UpdateGuestUserRequest(enabled=True),
            user_id=str(requester_id),
        )
    )

    assert captured["organization_id"] == str(org_id)
    assert guest_toggle_session.committed is True
    assert result == {"enabled": True}

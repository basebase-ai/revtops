import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from api.routes import auth


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _ListResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _ScalarsResult(self._values)


class _FakeSession:
    def __init__(self, *, users, membership, mappings):
        self._users = users
        self._membership = membership
        self._mappings = mappings
        self._execute_calls = 0
        self.committed = False

    async def get(self, _model, model_id):
        return self._users.get(model_id)

    async def execute(self, _query):
        self._execute_calls += 1
        if self._execute_calls == 1:
            return _ScalarResult(self._membership)
        if self._execute_calls == 2:
            return _ListResult(self._mappings)
        raise AssertionError(f"unexpected execute call {self._execute_calls}")

    async def commit(self):
        self.committed = True


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_remove_member_rejects_guest_user(monkeypatch):
    org_id = UUID("11111111-1111-1111-1111-111111111111")
    requester_id = UUID("22222222-2222-2222-2222-222222222222")
    guest_user_id = UUID("33333333-3333-3333-3333-333333333333")

    membership = SimpleNamespace(user_id=guest_user_id, organization_id=org_id, status="active")
    requester = SimpleNamespace(id=requester_id, is_guest=False)
    guest_user = SimpleNamespace(id=guest_user_id, is_guest=True, organization_id=org_id)

    fake_session = _FakeSession(users={requester_id: requester, guest_user_id: guest_user}, membership=membership, mappings=[])
    monkeypatch.setattr(auth, "get_admin_session", lambda: _FakeSessionContext(fake_session))

    async def _allow_admin(*_args, **_kwargs):
        return True

    monkeypatch.setattr(auth, "_can_administer_org", _allow_admin)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            auth.remove_organization_member(
                org_id=str(org_id),
                target_user_id=str(guest_user_id),
                user_id=str(requester_id),
            )
        )

    assert exc.value.status_code == 403
    assert not fake_session.committed


def test_remove_member_unlinks_all_identities(monkeypatch):
    org_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    requester_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    target_user_id = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")

    membership = SimpleNamespace(user_id=target_user_id, organization_id=org_id, status="active")
    requester = SimpleNamespace(id=requester_id, is_guest=False)
    target_user = SimpleNamespace(id=target_user_id, is_guest=False, organization_id=org_id)
    mappings = [
        SimpleNamespace(user_id=target_user_id, revtops_email="one@example.com", match_source="auto"),
        SimpleNamespace(user_id=target_user_id, revtops_email="two@example.com", match_source="auto"),
    ]

    fake_session = _FakeSession(
        users={requester_id: requester, target_user_id: target_user},
        membership=membership,
        mappings=mappings,
    )
    monkeypatch.setattr(auth, "get_admin_session", lambda: _FakeSessionContext(fake_session))

    async def _allow_admin(*_args, **_kwargs):
        return True

    async def _skip_admin_guard(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth, "_can_administer_org", _allow_admin)
    monkeypatch.setattr(auth, "_ensure_org_has_admin", _skip_admin_guard)

    result = asyncio.run(
        auth.remove_organization_member(
            org_id=str(org_id),
            target_user_id=str(target_user_id),
            user_id=str(requester_id),
        )
    )

    assert result["status"] == "removed"
    assert membership.status == "deactivated"
    assert target_user.organization_id is None
    assert fake_session.committed
    for mapping in mappings:
        assert mapping.user_id is None
        assert mapping.revtops_email is None
        assert mapping.match_source == "manual_unlink"

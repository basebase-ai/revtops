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
    def __init__(self, *, users, execute_results):
        self._users = users
        self._execute_results = list(execute_results)
        self._execute_calls = 0
        self.committed = False

    async def get(self, _model, model_id):
        return self._users.get(model_id)

    async def execute(self, _query):
        if self._execute_calls >= len(self._execute_results):
            raise AssertionError(f"unexpected execute call {self._execute_calls + 1}")
        result = self._execute_results[self._execute_calls]
        self._execute_calls += 1
        return result

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
    requester = SimpleNamespace(id=requester_id, is_guest=False, role="member", roles=[])
    guest_user = SimpleNamespace(id=guest_user_id, is_guest=True, organization_id=org_id)

    fake_session = _FakeSession(
        users={requester_id: requester, guest_user_id: guest_user},
        execute_results=[_ScalarResult(membership)],
    )
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
    requester = SimpleNamespace(id=requester_id, is_guest=False, role="member", roles=[])
    target_user = SimpleNamespace(
        id=target_user_id, is_guest=False, guest_organization_id=None
    )
    mappings = [
        SimpleNamespace(user_id=target_user_id, revtops_email="one@example.com", match_source="auto"),
        SimpleNamespace(user_id=target_user_id, revtops_email="two@example.com", match_source="auto"),
    ]

    fake_session = _FakeSession(
        users={requester_id: requester, target_user_id: target_user},
        execute_results=[_ScalarResult(membership), _ListResult(mappings)],
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
    assert target_user.guest_organization_id is None  # unchanged for non-guest
    assert fake_session.committed
    for mapping in mappings:
        assert mapping.user_id is None
        assert mapping.revtops_email is None
        assert mapping.match_source == "manual_unlink"


def test_remove_invited_member_rejects_non_admin_requester(monkeypatch):
    org_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    requester_id = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
    invited_user_id = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")

    requester = SimpleNamespace(id=requester_id, is_guest=False, role="member", roles=[])
    invited_user = SimpleNamespace(id=invited_user_id, is_guest=False, organization_id=org_id)
    invited_membership = SimpleNamespace(user_id=invited_user_id, organization_id=org_id, status="invited", role="member")

    fake_session = _FakeSession(
        users={requester_id: requester, invited_user_id: invited_user},
        execute_results=[_ScalarResult(invited_membership)],
    )
    monkeypatch.setattr(auth, "get_admin_session", lambda: _FakeSessionContext(fake_session))

    async def _deny_admin(*_args, **_kwargs):
        return False

    monkeypatch.setattr(auth, "_can_administer_org", _deny_admin)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            auth.remove_organization_member(
                org_id=str(org_id),
                target_user_id=str(invited_user_id),
                user_id=str(requester_id),
            )
        )

    assert exc.value.status_code == 403
    assert not fake_session.committed

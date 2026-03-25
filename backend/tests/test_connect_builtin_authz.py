import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from api.auth_middleware import AuthContext
from api.routes import auth


class _FakeMembershipResult:
    def scalar_one_or_none(self) -> object:
        return True  # Simulate active org membership

class _FakeSession:
    def __init__(self, *, users):
        self._users = users

    async def get(self, _model, model_id):
        return self._users.get(model_id)

    async def execute(self, _query, _params=None):
        return _FakeMembershipResult()


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _make_auth_context(*, user_id: UUID, organization_id: UUID) -> AuthContext:
    return AuthContext(
        user_id=user_id,
        organization_id=organization_id,
        email="user@example.com",
        role="member",
        is_global_admin=False,
    )


def test_connect_builtin_rejects_code_sandbox_for_non_admin(monkeypatch):
    org_id = UUID("11111111-1111-1111-1111-111111111111")
    user_id = UUID("22222222-2222-2222-2222-222222222222")
    requester = SimpleNamespace(
        id=user_id,
        organization_id=org_id,
        is_guest=False,
        role="member",
        roles=[],
    )

    monkeypatch.setattr(
        auth,
        "get_admin_session",
        lambda: _FakeSessionContext(_FakeSession(users={user_id: requester})),
    )

    async def _deny_admin(*_args, **_kwargs):
        return False

    monkeypatch.setattr(auth, "_can_administer_org", _deny_admin)

    request = auth.ConnectBuiltinRequest(
        organization_id=str(org_id),
        provider="code_sandbox",
        user_id=str(user_id),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            auth.connect_builtin(
                request,
                auth=_make_auth_context(user_id=user_id, organization_id=org_id),
            )
        )

    assert exc.value.status_code == 403
    assert "Code Sandbox" in exc.value.detail


def test_connect_builtin_allows_web_search_for_non_admin(monkeypatch):
    org_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    user_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    requester = SimpleNamespace(
        id=user_id,
        organization_id=org_id,
        is_guest=False,
        role="member",
        roles=[],
    )

    monkeypatch.setattr(
        auth,
        "get_admin_session",
        lambda: _FakeSessionContext(_FakeSession(users={user_id: requester})),
    )

    async def _unexpected_admin_check(*_args, **_kwargs):
        raise AssertionError("admin check should not run for web_search")

    monkeypatch.setattr(auth, "_can_administer_org", _unexpected_admin_check)

    class _ConnectSession:
        def __init__(self):
            self.executed = []
            self.added = []
            self.committed = False

        async def execute(self, query, params=None):
            self.executed.append((query, params))

            class _EmptyResult:
                def scalar_one_or_none(self_inner):
                    return None

            return _EmptyResult()

        def add(self, integration):
            self.added.append(integration)

        async def commit(self):
            self.committed = True

    connect_session = _ConnectSession()
    monkeypatch.setattr(
        auth,
        "get_session",
        lambda organization_id=None: _FakeSessionContext(connect_session),
    )

    request = auth.ConnectBuiltinRequest(
        organization_id=str(org_id),
        provider="web_search",
        user_id=str(user_id),
    )

    result = asyncio.run(
        auth.connect_builtin(
            request,
            auth=_make_auth_context(user_id=user_id, organization_id=org_id),
        )
    )

    assert result == {"status": "connected", "provider": "web_search"}
    assert connect_session.committed is True
    assert len(connect_session.added) == 1


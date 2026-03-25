import asyncio
from types import SimpleNamespace
from uuid import UUID

from api import auth_middleware


class _FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, target_user, default_org_id):
        self._target_user = target_user
        self._default_org_id = default_org_id

    async def get(self, _model, _id):
        return self._target_user

    async def execute(self, _query):
        return _FakeExecuteResult(self._default_org_id)


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_masquerade_defaults_to_target_active_org(monkeypatch):
    admin_user_id = UUID("11111111-1111-1111-1111-111111111111")
    target_user_id = UUID("22222222-2222-2222-2222-222222222222")
    target_org_id = UUID("33333333-3333-3333-3333-333333333333")

    admin_user = SimpleNamespace(
        id=admin_user_id,
        email="admin@example.com",
        role="global_admin",
        roles=["global_admin"],
        is_guest=False,
    )
    target_user = SimpleNamespace(
        id=target_user_id,
        email="target@example.com",
        role="user",
        roles=["user"],
        is_guest=False,
    )

    async def _verify_jwt(_token):
        return {"sub": str(admin_user_id)}

    async def _get_user_from_token(_payload):
        return admin_user

    monkeypatch.setattr(auth_middleware, "_verify_jwt", _verify_jwt)
    monkeypatch.setattr(auth_middleware, "_get_user_from_token", _get_user_from_token)

    fake_session = _FakeSession(target_user=target_user, default_org_id=target_org_id)

    def _fake_get_admin_session():
        return _FakeSessionContext(fake_session)

    import models.database as database_module

    monkeypatch.setattr(database_module, "get_admin_session", _fake_get_admin_session)

    auth = asyncio.run(
        auth_middleware.get_current_auth(
            authorization="Bearer token",
            masquerade_user_id=str(target_user_id),
            admin_user_id=str(admin_user_id),
            x_organization_id=None,
        )
    )

    assert auth.user_id == target_user_id
    assert auth.organization_id == target_org_id

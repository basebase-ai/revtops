import asyncio
from types import SimpleNamespace
from uuid import UUID

from api.routes import auth


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _MappingsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _ScalarsResult(self._values)


class _FakeSession:
    def __init__(self, *, org, execute_results):
        self._org = org
        self._execute_results = list(execute_results)
        self._execute_calls = 0

    async def get(self, _model, _model_id):
        return self._org

    async def execute(self, _query):
        if self._execute_calls >= len(self._execute_results):
            raise AssertionError(f"unexpected execute call {self._execute_calls + 1}")
        result = self._execute_results[self._execute_calls]
        self._execute_calls += 1
        return result


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_get_organization_members_keeps_guest_user_visible_when_disabled(monkeypatch):
    org_id = UUID("11111111-1111-1111-1111-111111111111")
    requester_id = UUID("22222222-2222-2222-2222-222222222222")
    guest_user_id = UUID("33333333-3333-3333-3333-333333333333")
    member_user_id = UUID("44444444-4444-4444-4444-444444444444")

    org = SimpleNamespace(id=org_id, guest_user_enabled=False)
    requester_membership = SimpleNamespace(user_id=requester_id, organization_id=org_id, status="active")
    guest_user = SimpleNamespace(
        id=guest_user_id,
        name="Guest User",
        email="guest@example.com",
        avatar_url=None,
        status="active",
        is_guest=True,
        role="member",
        roles=[],
    )
    member_user = SimpleNamespace(
        id=member_user_id,
        name="Member User",
        email="member@example.com",
        avatar_url=None,
        status="active",
        is_guest=False,
        role="member",
        roles=[],
    )
    guest_membership = SimpleNamespace(
        user_id=guest_user_id,
        organization_id=org_id,
        status="active",
        role="member",
        title=None,
    )
    member_membership = SimpleNamespace(
        user_id=member_user_id,
        organization_id=org_id,
        status="active",
        role="member",
        title=None,
    )

    fake_session = _FakeSession(
        org=org,
        execute_results=[
            _ScalarResult(requester_membership),
            _RowsResult([(guest_user, guest_membership), (member_user, member_membership)]),
            _MappingsResult([]),
        ],
    )
    monkeypatch.setattr(auth, "get_admin_session", lambda: _FakeSessionContext(fake_session))

    response = asyncio.run(
        auth.get_organization_members(
            org_id=str(org_id),
            auth=SimpleNamespace(user_id=requester_id),
        )
    )

    assert response.guest_user_enabled is False
    assert [member.id for member in response.members] == [str(guest_user_id), str(member_user_id)]
    assert response.members[0].is_guest is True
    assert response.members[0].email == "guest@example.com"

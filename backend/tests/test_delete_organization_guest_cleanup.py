import asyncio
from types import SimpleNamespace
from uuid import UUID

from api.routes import auth


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ExecResult:
    def __init__(self, rowcount=0):
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, *, membership, org):
        self._membership = membership
        self._org = org
        self.committed = False
        self.deleted = []
        self.statements = []

    async def execute(self, query, params=None):
        if not self.statements:
            self.statements.append((str(query), params))
            return _ScalarResult(self._membership)

        sql = str(query)
        self.statements.append((sql, params))
        if "DELETE FROM users WHERE organization_id = :org_id AND is_guest IS TRUE" in sql:
            return _ExecResult(rowcount=1)
        if "UPDATE users SET organization_id = NULL WHERE organization_id = :org_id AND is_guest IS NOT TRUE" in sql:
            return _ExecResult(rowcount=3)
        return _ExecResult(rowcount=0)

    async def get(self, _model, _id):
        return self._org

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_delete_organization_deletes_guest_users_before_detach(monkeypatch):
    org_id = UUID("11111111-1111-1111-1111-111111111111")
    requester_id = UUID("22222222-2222-2222-2222-222222222222")

    membership = SimpleNamespace(user_id=requester_id, organization_id=org_id, status="active", role="admin")
    org = SimpleNamespace(id=org_id)

    fake_session = _FakeSession(membership=membership, org=org)
    monkeypatch.setattr(auth, "get_admin_session", lambda: _FakeSessionContext(fake_session))

    result = asyncio.run(auth.delete_organization(org_id=str(org_id), user_id=str(requester_id)))

    assert result["status"] == "deleted"
    assert fake_session.committed
    assert fake_session.deleted == [org]

    sql_statements = [sql for sql, _params in fake_session.statements]
    guest_delete_ix = next(i for i, sql in enumerate(sql_statements) if "DELETE FROM users WHERE organization_id = :org_id AND is_guest IS TRUE" in sql)
    detach_ix = next(i for i, sql in enumerate(sql_statements) if "UPDATE users SET organization_id = NULL WHERE organization_id = :org_id AND is_guest IS NOT TRUE" in sql)
    assert guest_delete_ix < detach_ix

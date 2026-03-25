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
    def __init__(self, *, membership, org, user):
        self._membership = membership
        self._org = org
        self._user = user
        self.committed = False
        self.deleted = []
        self.statements = []

    async def execute(self, query, params=None):
        if not self.statements:
            self.statements.append((str(query), params))
            return _ScalarResult(self._membership)

        sql = str(query)
        self.statements.append((sql, params))
        if "SELECT to_regclass(:table_name)" in sql:
            table_name = (params or {}).get("table_name", "")
            if table_name.endswith("crm_operations"):
                return _ScalarResult(None)
            return _ScalarResult(table_name)
        if (
            "DELETE FROM users WHERE guest_organization_id = :org_id AND is_guest IS TRUE"
            in sql
        ):
            return _ExecResult(rowcount=1)
        return _ExecResult(rowcount=0)

    async def get(self, model, _id):
        model_name = model.__name__ if hasattr(model, '__name__') else str(model)
        if model_name == 'User':
            return self._user
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
    user = SimpleNamespace(id=requester_id, role="global_admin", roles=["global_admin"])

    fake_session = _FakeSession(membership=membership, org=org, user=user)
    monkeypatch.setattr(auth, "get_admin_session", lambda: _FakeSessionContext(fake_session))

    fake_auth = SimpleNamespace(user_id=requester_id, organization_id=org_id, email="admin@test.com", role="admin", is_global_admin=True)
    result = asyncio.run(auth.delete_organization(org_id=str(org_id), auth=fake_auth))

    assert result["status"] == "deleted"
    assert fake_session.committed
    assert fake_session.deleted == [org]

    sql_statements = [sql for sql, _params in fake_session.statements]
    assert any(
        "DELETE FROM users WHERE guest_organization_id = :org_id AND is_guest IS TRUE"
        in sql
        for sql in sql_statements
    )
    assert not any("DELETE FROM crm_operations" in sql for sql in sql_statements)
    assert any("DELETE FROM pending_operations" in sql for sql in sql_statements)

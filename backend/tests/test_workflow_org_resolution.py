import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

from workers.tasks import workflows


class _FakeResult:
    def __init__(self, value: UUID | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> UUID | None:
        return self._value


class _FakeAdminSession:
    def __init__(self, value: UUID | None) -> None:
        self._value = value

    async def execute(self, _query: object) -> _FakeResult:
        return _FakeResult(self._value)


def test_resolve_workflow_org_id_returns_passed_value_without_lookup() -> None:
    async def _run() -> str | None:
        return await workflows._resolve_workflow_organization_id(
            workflow_id="00000000-0000-0000-0000-000000000010",
            organization_id="00000000-0000-0000-0000-000000000099",
        )

    assert asyncio.run(_run()) == "00000000-0000-0000-0000-000000000099"


def test_resolve_workflow_org_id_looks_up_when_missing(monkeypatch) -> None:
    org_id = UUID("00000000-0000-0000-0000-0000000000aa")

    @asynccontextmanager
    async def _fake_get_admin_session():
        yield _FakeAdminSession(org_id)

    monkeypatch.setattr("models.database.get_admin_session", _fake_get_admin_session)

    async def _run() -> str | None:
        return await workflows._resolve_workflow_organization_id(
            workflow_id="00000000-0000-0000-0000-000000000010",
            organization_id=None,
        )

    assert asyncio.run(_run()) == str(org_id)


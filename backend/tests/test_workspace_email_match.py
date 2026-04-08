import asyncio
from types import SimpleNamespace
from uuid import UUID

from messengers._workspace import WorkspaceMessenger
from messengers.base import MessengerMeta, ResponseMode


class _TestWorkspaceMessenger(WorkspaceMessenger):
    meta = MessengerMeta(name="Test", slug="test", response_mode=ResponseMode.STREAMING)

    async def resolve_organization(self, user, message):  # type: ignore[override]
        raise NotImplementedError

    async def find_or_create_conversation(self, organization_id, user, message):  # type: ignore[override]
        raise NotImplementedError

    async def download_attachments(self, message):  # type: ignore[override]
        raise NotImplementedError

    def format_text(self, markdown: str) -> str:
        return markdown

    async def post_message(
        self,
        channel_id: str,
        text: str,
        thread_id: str | None = None,
        *,
        workspace_id: str | None = None,
        organization_id: str | None = None,
    ) -> str | None:
        raise NotImplementedError


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _query):
        return _FakeExecuteResult(self._rows)


class _FakeAdminSessionContext:
    def __init__(self, rows):
        self._session = _FakeSession(rows)

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_match_user_by_email_handles_duplicate_rows_without_error(monkeypatch):
    org_id = "11111111-1111-1111-1111-111111111111"
    member_user = SimpleNamespace(
        id=UUID("22222222-2222-2222-2222-222222222222"),
        is_guest=False,
    )
    guest_user = SimpleNamespace(
        id=UUID("33333333-3333-3333-3333-333333333333"),
        is_guest=True,
    )

    monkeypatch.setattr(
        "messengers._workspace.get_admin_session",
        lambda: _FakeAdminSessionContext([member_user, guest_user]),
    )

    messenger = _TestWorkspaceMessenger()
    resolved = asyncio.run(messenger._match_user_by_email(org_id, "person@example.com"))

    assert resolved is not None
    assert resolved.id == member_user.id

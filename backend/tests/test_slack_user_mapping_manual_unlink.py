import asyncio
from types import SimpleNamespace
from uuid import UUID

from services import slack_conversations


class _FakeExecuteResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    def __init__(self, rows):
        self._rows = list(rows)
        self.commit_calls = 0

    async def execute(self, _query):
        row = self._rows.pop(0) if self._rows else None
        return _FakeExecuteResult(row)

    async def commit(self):
        self.commit_calls += 1

    def add(self, _obj):
        raise AssertionError("add() should not be called when preserving manual_unlink")


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_upsert_for_user_does_not_promote_manual_unlink(monkeypatch):
    manual_unlink_mapping = SimpleNamespace(
        user_id=None,
        match_source="manual_unlink",
        external_email="old@example.com",
        source="slack",
        updated_at=None,
    )
    fake_session = _FakeSession(rows=[manual_unlink_mapping])
    monkeypatch.setattr(
        slack_conversations,
        "get_admin_session",
        lambda: _FakeSessionContext(fake_session),
    )

    asyncio.run(
        slack_conversations._upsert_slack_user_mapping(
            organization_id="11111111-1111-1111-1111-111111111111",
            user_id=UUID("22222222-2222-2222-2222-222222222222"),
            slack_user_id="U123",
            slack_email="new@example.com",
            match_source="slack_profile_email_match",
            revtops_email="owner@example.com",
        )
    )

    assert manual_unlink_mapping.user_id is None
    assert manual_unlink_mapping.match_source == "manual_unlink"
    assert fake_session.commit_calls == 0


def test_unmapped_upsert_does_not_mutate_manual_unlink(monkeypatch):
    manual_unlink_mapping = SimpleNamespace(
        user_id=None,
        match_source="manual_unlink",
        external_email="old@example.com",
        source="slack",
        updated_at=None,
    )
    fake_session = _FakeSession(rows=[manual_unlink_mapping])
    monkeypatch.setattr(
        slack_conversations,
        "get_admin_session",
        lambda: _FakeSessionContext(fake_session),
    )

    asyncio.run(
        slack_conversations._upsert_slack_user_mapping(
            organization_id="11111111-1111-1111-1111-111111111111",
            user_id=None,
            slack_user_id="U123",
            slack_email="new@example.com",
            match_source="slack_profile_email_match",
        )
    )

    assert manual_unlink_mapping.external_email == "old@example.com"
    assert manual_unlink_mapping.match_source == "manual_unlink"
    assert fake_session.commit_calls == 0

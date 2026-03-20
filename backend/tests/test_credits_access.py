import asyncio
from types import SimpleNamespace

from services import credits


class _ExecResult:
    def __init__(self, row):
        self._row = row

    def one_or_none(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self._row = row

    async def execute(self, _query):
        return _ExecResult(self._row)


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_can_use_credits_allows_pending_billing_with_balance(monkeypatch) -> None:
    row = SimpleNamespace(
        subscription_status="past_due",
        credits_balance=credits.MIN_CREDITS_TO_START,
    )
    monkeypatch.setattr(
        credits,
        "get_admin_session",
        lambda: _FakeSessionContext(_FakeSession(row)),
    )

    allowed = asyncio.run(credits.can_use_credits("00000000-0000-0000-0000-000000000001"))

    assert allowed is True


def test_can_use_credits_blocks_pending_billing_without_enough_balance(monkeypatch) -> None:
    row = SimpleNamespace(
        subscription_status="past_due",
        credits_balance=credits.MIN_CREDITS_TO_START - 1,
    )
    monkeypatch.setattr(
        credits,
        "get_admin_session",
        lambda: _FakeSessionContext(_FakeSession(row)),
    )

    allowed = asyncio.run(credits.can_use_credits("00000000-0000-0000-0000-000000000001"))

    assert allowed is False


def test_can_use_credits_blocks_canceled_subscription_even_with_balance(monkeypatch) -> None:
    row = SimpleNamespace(
        subscription_status="canceled",
        credits_balance=credits.MIN_CREDITS_TO_START + 20,
    )
    monkeypatch.setattr(
        credits,
        "get_admin_session",
        lambda: _FakeSessionContext(_FakeSession(row)),
    )

    allowed = asyncio.run(credits.can_use_credits("00000000-0000-0000-0000-000000000001"))

    assert allowed is False

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import services.workflow_pause as workflow_pause


class _FakeRedisClient:
    def __init__(self, stored_value: str | None = None) -> None:
        self.stored_value = stored_value
        self.set_calls: list[tuple[str, str, int]] = []

    async def __aenter__(self) -> "_FakeRedisClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def set(self, key: str, value: str, ex: int) -> None:
        self.set_calls.append((key, value, ex))
        self.stored_value = value

    async def get(self, key: str) -> str | None:
        return self.stored_value


@pytest.mark.asyncio
async def test_pause_workflow_execution_for_seconds_sets_pause_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeRedisClient()
    monkeypatch.setattr(workflow_pause.aioredis, "from_url", lambda *args, **kwargs: fake_client)

    pause_until = await workflow_pause.pause_workflow_execution_for_seconds(seconds=60)

    assert fake_client.set_calls
    key, value, ttl = fake_client.set_calls[0]
    assert key == workflow_pause.WORKFLOW_EXECUTION_PAUSE_KEY
    assert ttl == 60
    assert float(value) > datetime.now(timezone.utc).timestamp()
    assert pause_until > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_get_workflow_execution_pause_until_returns_none_for_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    expired = (datetime.now(timezone.utc) - timedelta(seconds=5)).timestamp()
    fake_client = _FakeRedisClient(stored_value=str(expired))
    monkeypatch.setattr(workflow_pause.aioredis, "from_url", lambda *args, **kwargs: fake_client)

    pause_until = await workflow_pause.get_workflow_execution_pause_until()

    assert pause_until is None


@pytest.mark.asyncio
async def test_is_workflow_execution_paused_true_when_pause_in_future(monkeypatch: pytest.MonkeyPatch) -> None:
    future = (datetime.now(timezone.utc) + timedelta(seconds=30)).timestamp()
    fake_client = _FakeRedisClient(stored_value=str(future))
    monkeypatch.setattr(workflow_pause.aioredis, "from_url", lambda *args, **kwargs: fake_client)

    assert await workflow_pause.is_workflow_execution_paused() is True

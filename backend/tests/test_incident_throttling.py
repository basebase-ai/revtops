from __future__ import annotations

from typing import Any

import pytest

from services import incident_throttling as throttling


class _FakeRedis:
    data: dict[str, dict[str, str]] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def __aenter__(self) -> "_FakeRedis":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.data.get(key, {}))

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        current = self.data.setdefault(key, {})
        current.update(mapping)

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def delete(self, key: str) -> int:
        existed = key in self.data
        self.data.pop(key, None)
        return 1 if existed else 0


@pytest.fixture(autouse=True)
def _reset_fake_redis() -> None:
    _FakeRedis.data = {}


def test_evaluate_incident_creation_first_failure_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(throttling.aioredis, "from_url", lambda *args, **kwargs: _FakeRedis())
    monkeypatch.setattr(throttling.time, "time", lambda: 1000)

    import asyncio

    should_create, reason = asyncio.run(throttling.evaluate_incident_creation("Auth JWKS"))

    assert should_create is True
    assert reason == "new_failure"


def test_evaluate_incident_creation_suppresses_repeated_failure_within_90m(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(throttling.aioredis, "from_url", lambda *args, **kwargs: _FakeRedis())

    import asyncio

    monkeypatch.setattr(throttling.time, "time", lambda: 1000)
    asyncio.run(throttling.evaluate_incident_creation("Auth JWKS"))

    monkeypatch.setattr(throttling.time, "time", lambda: 1000 + 120)
    should_create, reason = asyncio.run(throttling.evaluate_incident_creation("Auth JWKS"))

    assert should_create is False
    assert reason.startswith("suppressed_for_")


def test_evaluate_incident_creation_allows_after_90m(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(throttling.aioredis, "from_url", lambda *args, **kwargs: _FakeRedis())

    import asyncio

    monkeypatch.setattr(throttling.time, "time", lambda: 1000)
    asyncio.run(throttling.evaluate_incident_creation("Auth JWKS"))

    monkeypatch.setattr(throttling.time, "time", lambda: 1000 + (90 * 60))
    should_create, reason = asyncio.run(throttling.evaluate_incident_creation("Auth JWKS"))

    assert should_create is True
    assert reason == "cooldown_elapsed"


def test_clear_incident_failure_removes_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(throttling.aioredis, "from_url", lambda *args, **kwargs: _FakeRedis())

    import asyncio

    monkeypatch.setattr(throttling.time, "time", lambda: 1000)
    asyncio.run(throttling.evaluate_incident_creation("Auth JWKS"))

    asyncio.run(throttling.clear_incident_failure("Auth JWKS"))
    should_create, reason = asyncio.run(throttling.evaluate_incident_creation("Auth JWKS"))

    assert should_create is True
    assert reason == "new_failure"

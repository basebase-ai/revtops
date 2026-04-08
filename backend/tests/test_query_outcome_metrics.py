import asyncio

from messengers.base import BaseMessenger
from services import query_outcome_metrics


def test_successful_query_outcome_classification() -> None:
    assert BaseMessenger._is_successful_query_outcome(
        result={"status": "success"},
        error=None,
    )
    assert BaseMessenger._is_successful_query_outcome(
        result={"status": "rejected", "reason": "unknown_user"},
        error=None,
    )
    assert BaseMessenger._is_successful_query_outcome(
        result={"status": "error", "error": "insufficient_credits"},
        error=None,
    )


def test_failed_query_outcome_classification() -> None:
    assert not BaseMessenger._is_successful_query_outcome(
        result={"status": "error", "error": "no_organization"},
        error=None,
    )
    assert not BaseMessenger._is_successful_query_outcome(
        result={"status": "timeout_continuing"},
        error=None,
    )
    assert not BaseMessenger._is_successful_query_outcome(
        result={"status": "success"},
        error=RuntimeError("boom"),
    )


def test_get_query_outcome_window_stats() -> None:
    class _FakePipeline:
        def zremrangebyscore(self, *_args, **_kwargs) -> None:
            return None

        def zcard(self, *_args, **_kwargs) -> None:
            return None

        async def execute(self) -> list[int]:
            return [0, 0, 9, 1]

    class _FakeRedis:
        def pipeline(self) -> _FakePipeline:
            return _FakePipeline()

        async def __aenter__(self) -> "_FakeRedis":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    original_from_url = query_outcome_metrics.aioredis.from_url
    query_outcome_metrics.aioredis.from_url = lambda *_args, **_kwargs: _FakeRedis()
    try:
        stats = asyncio.run(query_outcome_metrics.get_query_outcome_window_stats())
    finally:
        query_outcome_metrics.aioredis.from_url = original_from_url

    assert stats["window_seconds"] == 1800
    assert stats["success_count"] == 9
    assert stats["failure_count"] == 1
    assert stats["total_count"] == 10
    assert stats["success_rate_pct"] == 90.0


def test_get_query_outcome_window_stats_defaults_to_full_success_for_empty_window() -> None:
    class _FakePipeline:
        def zremrangebyscore(self, *_args, **_kwargs) -> None:
            return None

        def zcard(self, *_args, **_kwargs) -> None:
            return None

        async def execute(self) -> list[int]:
            return [0, 0, 0, 0]

    class _FakeRedis:
        def pipeline(self) -> _FakePipeline:
            return _FakePipeline()

        async def __aenter__(self) -> "_FakeRedis":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    original_from_url = query_outcome_metrics.aioredis.from_url
    query_outcome_metrics.aioredis.from_url = lambda *_args, **_kwargs: _FakeRedis()
    try:
        stats = asyncio.run(query_outcome_metrics.get_query_outcome_window_stats())
    finally:
        query_outcome_metrics.aioredis.from_url = original_from_url

    assert stats["window_seconds"] == 1800
    assert stats["success_count"] == 0
    assert stats["failure_count"] == 0
    assert stats["total_count"] == 0
    assert stats["success_rate_pct"] == 100.0


def test_record_query_outcome_raises_incident_when_success_rate_at_or_below_25(
    monkeypatch,
) -> None:
    class _FakePipeline:
        def __init__(self) -> None:
            self._zcard_results = [1, 3]

        def zadd(self, *_args, **_kwargs) -> None:
            return None

        def expire(self, *_args, **_kwargs) -> None:
            return None

        def zremrangebyscore(self, *_args, **_kwargs) -> None:
            return None

        def zcard(self, *_args, **_kwargs) -> None:
            return None

        async def execute(self):
            if self._zcard_results:
                return [0, 0, self._zcard_results.pop(0), self._zcard_results.pop(0)]
            return [1, True]

    class _FakeRedis:
        def pipeline(self) -> _FakePipeline:
            return _FakePipeline()

        async def __aenter__(self) -> "_FakeRedis":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    incidents: list[dict[str, str]] = []
    evaluate_calls: list[str] = []
    clear_calls: list[str] = []

    async def _fake_evaluate(check_name: str) -> tuple[bool, str]:
        evaluate_calls.append(check_name)
        return True, "new_failure"

    async def _fake_incident(*, title: str, details: str) -> bool:
        incidents.append({"title": title, "details": details})
        return True

    async def _fake_clear(check_name: str) -> None:
        clear_calls.append(check_name)

    monkeypatch.setattr(query_outcome_metrics.aioredis, "from_url", lambda *_args, **_kwargs: _FakeRedis())
    monkeypatch.setattr(query_outcome_metrics, "evaluate_incident_creation", _fake_evaluate)
    monkeypatch.setattr(query_outcome_metrics, "create_pagerduty_incident", _fake_incident)
    monkeypatch.setattr(query_outcome_metrics, "clear_incident_failure", _fake_clear)

    asyncio.run(query_outcome_metrics.record_query_outcome(platform="slack", was_success=True))

    assert evaluate_calls == ["Rolling Query Success"]
    assert clear_calls == []
    assert len(incidents) == 1
    assert incidents[0]["title"] == "Rolling query success dropped to 25% or below"


def test_record_query_outcome_clears_throttle_when_success_rate_recovers(monkeypatch) -> None:
    class _FakePipeline:
        def __init__(self) -> None:
            self._zcard_results = [3, 1]

        def zadd(self, *_args, **_kwargs) -> None:
            return None

        def expire(self, *_args, **_kwargs) -> None:
            return None

        def zremrangebyscore(self, *_args, **_kwargs) -> None:
            return None

        def zcard(self, *_args, **_kwargs) -> None:
            return None

        async def execute(self):
            if self._zcard_results:
                return [0, 0, self._zcard_results.pop(0), self._zcard_results.pop(0)]
            return [1, True]

    class _FakeRedis:
        def pipeline(self) -> _FakePipeline:
            return _FakePipeline()

        async def __aenter__(self) -> "_FakeRedis":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    evaluate_calls: list[str] = []
    incident_calls: list[str] = []
    clear_calls: list[str] = []

    async def _fake_evaluate(check_name: str) -> tuple[bool, str]:
        evaluate_calls.append(check_name)
        return True, "new_failure"

    async def _fake_incident(*, title: str, details: str) -> bool:
        incident_calls.append(title)
        return True

    async def _fake_clear(check_name: str) -> None:
        clear_calls.append(check_name)

    monkeypatch.setattr(query_outcome_metrics.aioredis, "from_url", lambda *_args, **_kwargs: _FakeRedis())
    monkeypatch.setattr(query_outcome_metrics, "evaluate_incident_creation", _fake_evaluate)
    monkeypatch.setattr(query_outcome_metrics, "create_pagerduty_incident", _fake_incident)
    monkeypatch.setattr(query_outcome_metrics, "clear_incident_failure", _fake_clear)

    asyncio.run(query_outcome_metrics.record_query_outcome(platform="slack", was_success=True))

    assert evaluate_calls == []
    assert incident_calls == []
    assert clear_calls == ["Rolling Query Success"]

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

    assert stats["window_seconds"] == 900
    assert stats["success_count"] == 9
    assert stats["failure_count"] == 1
    assert stats["total_count"] == 10
    assert stats["success_rate_pct"] == 90.0

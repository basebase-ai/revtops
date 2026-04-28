import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from workers.tasks import sync as sync_tasks


class FailingConnector:
    def __init__(
        self,
        organization_id: str,
        user_id: Optional[str] = None,
        *,
        sync_since_override: datetime | None = None,
    ) -> None:
        self.organization_id = organization_id
        self.user_id = user_id

    async def sync_all(self) -> dict[str, int]:
        raise RuntimeError("Slack API error: invalid_auth for test")

    async def mark_sync_started(self) -> None:
        return None

    async def clear_sync_started(self) -> None:
        return None

    async def update_last_sync(self, counts: dict[str, int]) -> None:
        return None

    async def record_error(self, error: str) -> None:
        return None


def test_classify_sync_failure_common_cases() -> None:
    assert sync_tasks._classify_sync_failure("invalid_auth on upstream")[0] == "auth_or_connection_revoked"
    assert sync_tasks._classify_sync_failure("429 Too Many Requests")[0] == "upstream_rate_limited"
    assert sync_tasks._classify_sync_failure("read timeout from upstream")[0] == "upstream_transient_error"
    assert sync_tasks._classify_sync_failure("TimeoutError args=()")[0] == "upstream_transient_error"
    assert sync_tasks._classify_sync_failure("totally novel failure")[0] == "unexpected_failure"


def test_should_retry_sync_failure_only_for_transient_cases() -> None:
    assert sync_tasks._should_retry_sync_failure("upstream_transient_error") is True
    assert sync_tasks._should_retry_sync_failure("upstream_rate_limited") is True
    assert sync_tasks._should_retry_sync_failure("auth_or_connection_revoked") is False
    assert sync_tasks._should_retry_sync_failure("unexpected_failure") is False


def test_compute_sync_retry_delay_seconds_uses_backoff_and_cap(monkeypatch) -> None:
    monkeypatch.setattr(sync_tasks.random, "randint", lambda _a, _b: 0)
    assert sync_tasks._compute_sync_retry_delay_seconds(0) == 30
    assert sync_tasks._compute_sync_retry_delay_seconds(1) == 60
    assert sync_tasks._compute_sync_retry_delay_seconds(2) == 120
    # capped to 300 + jitter
    assert sync_tasks._compute_sync_retry_delay_seconds(10) == 300


def test_sync_failure_logging_includes_case_and_context(monkeypatch, caplog) -> None:
    emitted_events: list[tuple[str, str, dict[str, Any]]] = []

    async def _emit_event(event_type: str, organization_id: str, data: dict[str, Any]) -> None:
        emitted_events.append((event_type, organization_id, data))

    async def _noop_clear_last_errors(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr("workers.events.emit_event", _emit_event)
    monkeypatch.setattr(sync_tasks, "_clear_last_errors_for_integration", _noop_clear_last_errors)
    monkeypatch.setattr(
        "connectors.registry.discover_connectors",
        lambda: {"slack": FailingConnector},
    )

    with caplog.at_level(logging.DEBUG):
        result = asyncio.run(
            sync_tasks._sync_integration("11111111-1111-1111-1111-111111111111", "slack")
        )

    assert result["status"] == "failed"
    assert any(
        "Connector sync failed provider=slack" in rec.message
        and "case=auth_or_connection_revoked" in rec.message
        for rec in caplog.records
    )
    assert any(
        "Connector sync failure diagnostics provider=slack" in rec.message
        and "error_type=RuntimeError" in rec.message
        for rec in caplog.records
    )
    assert any(event[0] == "sync.failed" for event in emitted_events)


class EmptyTimeoutConnector:
    def __init__(
        self,
        organization_id: str,
        user_id: Optional[str] = None,
        *,
        sync_since_override: datetime | None = None,
    ) -> None:
        self.organization_id = organization_id
        self.user_id = user_id

    async def sync_all(self) -> dict[str, int]:
        raise TimeoutError()

    async def mark_sync_started(self) -> None:
        return None

    async def clear_sync_started(self) -> None:
        return None

    async def update_last_sync(self, counts: dict[str, int]) -> None:
        return None

    async def record_error(self, error: str) -> None:
        return None


def test_empty_timeout_error_is_logged_and_classified_retryable(monkeypatch, caplog) -> None:
    async def _emit_event(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _noop_clear_last_errors(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr("workers.events.emit_event", _emit_event)
    monkeypatch.setattr(sync_tasks, "_clear_last_errors_for_integration", _noop_clear_last_errors)
    monkeypatch.setattr(
        "connectors.registry.discover_connectors",
        lambda: {"fireflies": EmptyTimeoutConnector},
    )

    with caplog.at_level(logging.DEBUG):
        result = asyncio.run(
            sync_tasks._sync_integration("11111111-1111-1111-1111-111111111111", "fireflies")
        )

    assert result["status"] == "failed"
    assert result["error"] == "TimeoutError args=()"
    assert any(
        "Connector sync raised exception with empty message provider=fireflies" in rec.message
        and "failure_case=upstream_transient_error" in rec.message
        and "retryable=True" in rec.message
        for rec in caplog.records
    )
    assert any(
        "Connector sync failed provider=fireflies" in rec.message
        and "case=upstream_transient_error" in rec.message
        for rec in caplog.records
    )

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
    assert sync_tasks._classify_sync_failure("totally novel failure")[0] == "unexpected_failure"


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

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(
            sync_tasks._sync_integration("11111111-1111-1111-1111-111111111111", "slack")
        )

    assert result["status"] == "failed"
    assert any(
        "Connector sync failed provider=slack" in rec.message
        and "case=auth_or_connection_revoked" in rec.message
        for rec in caplog.records
    )
    assert any(event[0] == "sync.failed" for event in emitted_events)

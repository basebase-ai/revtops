import asyncio
from datetime import datetime
from typing import Any, Optional

from connectors.base import SyncCancelledError
from workers.tasks import sync as sync_tasks


class CancelledConnector:
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
        raise SyncCancelledError("hubspot integration disconnected during sync (sync_all:after_accounts)")

    async def mark_sync_started(self) -> None:
        return None

    async def clear_sync_started(self) -> None:
        return None

    async def update_last_sync(self, counts: dict[str, int]) -> None:
        return None

    async def record_error(self, error: str) -> None:
        return None


def test_celery_sync_returns_cancelled_when_connector_disconnects(monkeypatch) -> None:
    """Patch discover_connectors so _sync_integration uses CancelledConnector for hubspot."""
    emitted_events: list[tuple[str, str, dict[str, Any]]] = []

    async def _emit_event(event_type: str, organization_id: str, data: dict[str, Any]) -> None:
        emitted_events.append((event_type, organization_id, data))

    monkeypatch.setattr("workers.events.emit_event", _emit_event)
    monkeypatch.setattr(
        "connectors.registry.discover_connectors",
        lambda: {"hubspot": CancelledConnector},
    )

    result = asyncio.run(sync_tasks._sync_integration("11111111-1111-1111-1111-111111111111", "hubspot"))

    assert result["status"] == "cancelled"
    assert result["provider"] == "hubspot"
    assert "disconnected during sync" in result["error"]
    assert any(event[0] == "sync.cancelled" for event in emitted_events)

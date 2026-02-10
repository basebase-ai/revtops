import asyncio

from api.routes import sync as sync_routes
from connectors.base import SyncCancelledError
from workers.tasks import sync as sync_tasks


class CancelledConnector:
    def __init__(self, organization_id: str) -> None:
        self.organization_id = organization_id

    async def sync_all(self) -> dict[str, int]:
        raise SyncCancelledError("hubspot integration disconnected during sync (sync_all:after_accounts)")

    async def update_last_sync(self, counts: dict[str, int]) -> None:
        return None

    async def record_error(self, error: str) -> None:
        return None


def test_celery_sync_returns_cancelled_when_connector_disconnects(monkeypatch) -> None:
    from connectors import hubspot

    monkeypatch.setattr(hubspot, "HubSpotConnector", CancelledConnector)

    result = asyncio.run(sync_tasks._sync_integration("11111111-1111-1111-1111-111111111111", "hubspot"))

    assert result["status"] == "cancelled"
    assert result["provider"] == "hubspot"
    assert "disconnected during sync" in result["error"]


def test_api_sync_status_marks_cancelled_when_connector_disconnects(monkeypatch) -> None:
    provider = "test_cancel"
    org_id = "11111111-1111-1111-1111-111111111111"
    status_key = f"{org_id}:{provider}"

    monkeypatch.setitem(sync_routes.CONNECTORS, provider, CancelledConnector)

    emitted_events: list[tuple[str, str, dict[str, str]]] = []

    async def _emit_event(event_type: str, organization_id: str, data: dict[str, str]) -> None:
        emitted_events.append((event_type, organization_id, data))

    monkeypatch.setattr("workers.events.emit_event", _emit_event)

    sync_routes._sync_status[status_key] = {
        "status": "syncing",
        "started_at": None,
        "completed_at": None,
        "error": None,
        "counts": None,
    }

    asyncio.run(sync_routes.sync_integration_data(org_id, provider))

    assert sync_routes._sync_status[status_key]["status"] == "cancelled"
    assert "disconnected during sync" in str(sync_routes._sync_status[status_key]["error"])
    assert emitted_events
    assert emitted_events[0][0] == "sync.cancelled"

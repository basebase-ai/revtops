"""Tests for the BaseConnector.sync_since incremental-sync property."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from connectors.base import BaseConnector


class _StubConnector(BaseConnector):
    """Minimal concrete connector for testing base-class behaviour."""

    source_system = "test"

    async def sync_deals(self) -> int:
        return 0

    async def sync_accounts(self) -> int:
        return 0

    async def sync_contacts(self) -> int:
        return 0

    async def sync_activities(self) -> int:
        return 0

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        return {"id": deal_id}


_ORG_ID: str = "00000000-0000-0000-0000-000000000000"


def _make_connector(last_sync_at: datetime | None = None) -> _StubConnector:
    """Create a _StubConnector with a fake integration attached."""
    connector = _StubConnector(organization_id=_ORG_ID)
    if last_sync_at is not None:
        connector._integration = SimpleNamespace(last_sync_at=last_sync_at, is_active=True)  # type: ignore[assignment]
    return connector


class TestSyncSinceProperty:
    """sync_since should return a buffered cutoff or None."""

    def test_returns_none_when_no_integration(self) -> None:
        connector = _StubConnector(organization_id=_ORG_ID)
        assert connector.sync_since is None

    def test_returns_none_when_last_sync_at_is_none(self) -> None:
        connector = _make_connector()
        connector._integration = SimpleNamespace(last_sync_at=None, is_active=True)  # type: ignore[assignment]
        assert connector.sync_since is None

    def test_returns_buffered_timestamp(self) -> None:
        now: datetime = datetime(2025, 6, 15, 12, 0, 0)
        connector = _make_connector(last_sync_at=now)
        expected: datetime = now - timedelta(minutes=5)

        result: datetime | None = connector.sync_since
        assert result is not None
        assert result == expected

    def test_buffer_equals_five_minutes(self) -> None:
        assert BaseConnector._SYNC_SINCE_BUFFER == timedelta(minutes=5)

    def test_sync_since_is_before_last_sync_at(self) -> None:
        now: datetime = datetime.utcnow()
        connector = _make_connector(last_sync_at=now)

        result: datetime | None = connector.sync_since
        assert result is not None
        assert result < now

    def test_different_last_sync_at_values(self) -> None:
        timestamps: list[datetime] = [
            datetime(2024, 1, 1, 0, 0, 0),
            datetime(2025, 6, 15, 23, 59, 59),
            datetime(2025, 12, 31, 0, 5, 0),
        ]
        buffer: timedelta = timedelta(minutes=5)
        for ts in timestamps:
            connector = _make_connector(last_sync_at=ts)
            assert connector.sync_since == ts - buffer

    def test_last_sync_at_very_recent(self) -> None:
        """Buffer should push the cutoff into the past even for a just-now timestamp."""
        now: datetime = datetime.utcnow()
        connector = _make_connector(last_sync_at=now)
        result: datetime | None = connector.sync_since
        assert result is not None
        assert (now - result).total_seconds() == 300.0

    def test_last_sync_at_exactly_at_buffer_boundary(self) -> None:
        """When last_sync_at is exactly 5 min after epoch, sync_since should equal epoch."""
        epoch_plus_five: datetime = datetime(1970, 1, 1, 0, 5, 0)
        connector = _make_connector(last_sync_at=epoch_plus_five)
        assert connector.sync_since == datetime(1970, 1, 1, 0, 0, 0)

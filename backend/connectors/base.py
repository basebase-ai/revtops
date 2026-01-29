"""
Base connector class that all connectors inherit from.

Uses Nango for OAuth token management - tokens are fetched from Nango
on demand and automatically refreshed.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select

from config import get_nango_integration_id
from models.database import get_session
from models.integration import Integration
from services.nango import get_nango_client


class BaseConnector(ABC):
    """Abstract base class for data source connectors."""

    # Override in subclasses - must match our provider names
    source_system: str = "unknown"

    def __init__(self, organization_id: str) -> None:
        """
        Initialize the connector.

        Args:
            organization_id: UUID of the organization to sync data for
        """
        self.organization_id = organization_id
        self._token: Optional[str] = None
        self._credentials: Optional[dict[str, Any]] = None
        self._integration: Optional[Integration] = None

    @abstractmethod
    async def sync_deals(self) -> int:
        """Fetch and normalize deals, return count synced."""
        pass

    @abstractmethod
    async def sync_accounts(self) -> int:
        """Fetch and normalize accounts, return count synced."""
        pass

    @abstractmethod
    async def sync_contacts(self) -> int:
        """Fetch and normalize contacts, return count synced."""
        pass

    @abstractmethod
    async def sync_activities(self) -> int:
        """Fetch and normalize activities, return count synced."""
        pass

    @abstractmethod
    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        """Fetch single deal on-demand."""
        pass

    async def sync_pipelines(self) -> int:
        """
        Fetch and normalize pipelines, return count synced.

        Override in subclasses that support pipelines (HubSpot, Salesforce).
        Default implementation returns 0.
        """
        return 0

    async def sync_all(self) -> dict[str, int]:
        """
        Run all sync operations.

        Returns:
            Dictionary with counts of synced records by type
        """
        # Sync pipelines first so deals can reference them
        pipelines_count = await self.sync_pipelines()
        accounts_count = await self.sync_accounts()
        deals_count = await self.sync_deals()
        contacts_count = await self.sync_contacts()
        activities_count = await self.sync_activities()

        result: dict[str, int] = {
            "accounts": accounts_count,
            "deals": deals_count,
            "contacts": contacts_count,
            "activities": activities_count,
        }

        # Only include pipelines if synced (not all connectors have them)
        if pipelines_count > 0:
            result["pipelines"] = pipelines_count

        return result

    async def get_oauth_token(self) -> tuple[str, str]:
        """
        Retrieve OAuth token from Nango.

        Nango handles token refresh automatically.

        Returns:
            Tuple of (access_token, instance_url_or_empty)
        """
        if self._token:
            return self._token, ""

        # Verify we have an active integration
        async with get_session() as session:
            result = await session.execute(
                select(Integration).where(
                    Integration.organization_id == UUID(self.organization_id),
                    Integration.provider == self.source_system,
                    Integration.is_active == True,
                )
            )
            integration = result.scalar_one_or_none()

            if not integration:
                raise ValueError(
                    f"No active {self.source_system} integration for organization: {self.organization_id}"
                )

            self._integration = integration

        # Get token from Nango
        nango = get_nango_client()
        nango_integration_id = get_nango_integration_id(self.source_system)

        # Use the actual Nango connection ID from the integration record
        connection_id = self._integration.nango_connection_id
        if not connection_id:
            raise ValueError(
                f"No Nango connection ID stored for {self.source_system} integration"
            )

        try:
            self._token = await nango.get_token(nango_integration_id, connection_id)
            return self._token, ""
        except Exception as e:
            raise ValueError(
                f"Failed to get token from Nango for {self.source_system}: {str(e)}"
            )

    async def get_credentials(self) -> dict[str, Any]:
        """
        Get full credentials from Nango.

        Useful when you need more than just the access token
        (e.g., instance URL, workspace ID, etc.)

        Returns:
            Full credentials dict from Nango
        """
        if self._credentials:
            return self._credentials

        # Ensure integration is loaded
        if not self._integration:
            await self.get_token()

        nango = get_nango_client()
        nango_integration_id = get_nango_integration_id(self.source_system)
        
        # Use the actual Nango connection ID from the integration record
        connection_id = self._integration.nango_connection_id
        if not connection_id:
            raise ValueError(
                f"No Nango connection ID stored for {self.source_system} integration"
            )

        self._credentials = await nango.get_credentials(
            nango_integration_id, connection_id
        )
        return self._credentials

    async def update_last_sync(self, counts: Optional[dict[str, int]] = None) -> None:
        """Update the last_sync_at timestamp and sync stats for this integration.
        
        Args:
            counts: Optional dictionary of object counts synced (e.g., {"accounts": 5, "deals": 10})
        """
        from datetime import datetime

        if not self._integration:
            # Try to load integration
            async with get_session() as session:
                result = await session.execute(
                    select(Integration).where(
                        Integration.organization_id == UUID(self.organization_id),
                        Integration.provider == self.source_system,
                    )
                )
                self._integration = result.scalar_one_or_none()

        if not self._integration:
            return

        async with get_session() as session:
            integration = await session.get(Integration, self._integration.id)
            if integration:
                integration.last_sync_at = datetime.utcnow()
                integration.last_error = None
                if counts is not None:
                    integration.sync_stats = counts
                await session.commit()

    async def record_error(self, error: str) -> None:
        """Record an error for this integration."""
        if not self._integration:
            return

        async with get_session() as session:
            integration = await session.get(Integration, self._integration.id)
            if integration:
                integration.last_error = error[:500]  # Truncate long errors
                await session.commit()

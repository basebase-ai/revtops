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

    def __init__(self, customer_id: str) -> None:
        """
        Initialize the connector.

        Args:
            customer_id: UUID of the customer to sync data for
        """
        self.customer_id = customer_id
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

    async def sync_all(self) -> dict[str, int]:
        """
        Run all sync operations.

        Returns:
            Dictionary with counts of synced records by type
        """
        accounts_count = await self.sync_accounts()
        deals_count = await self.sync_deals()
        contacts_count = await self.sync_contacts()
        activities_count = await self.sync_activities()

        return {
            "accounts": accounts_count,
            "deals": deals_count,
            "contacts": contacts_count,
            "activities": activities_count,
        }

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
                    Integration.customer_id == UUID(self.customer_id),
                    Integration.provider == self.source_system,
                    Integration.is_active == True,
                )
            )
            integration = result.scalar_one_or_none()

            if not integration:
                raise ValueError(
                    f"No active {self.source_system} integration for customer: {self.customer_id}"
                )

            self._integration = integration

        # Get token from Nango
        nango = get_nango_client()
        nango_integration_id = get_nango_integration_id(self.source_system)

        # Use customer_id as the Nango connection_id
        connection_id = self.customer_id

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

        nango = get_nango_client()
        nango_integration_id = get_nango_integration_id(self.source_system)
        connection_id = self.customer_id

        self._credentials = await nango.get_credentials(
            nango_integration_id, connection_id
        )
        return self._credentials

    async def update_last_sync(self) -> None:
        """Update the last_sync_at timestamp for this integration."""
        from datetime import datetime

        if not self._integration:
            # Try to load integration
            async with get_session() as session:
                result = await session.execute(
                    select(Integration).where(
                        Integration.customer_id == UUID(self.customer_id),
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

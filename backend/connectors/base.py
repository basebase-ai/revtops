"""
Base connector class that all connectors inherit from.

Uses Nango for OAuth token management - tokens are fetched from Nango
on demand and automatically refreshed.
"""

from abc import ABC, abstractmethod
import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select

from config import get_nango_integration_id
from connectors.registry import ConnectorMeta  # noqa: F401 – re-export for convenience
from models.database import get_session
from models.integration import Integration
from services.nango import get_nango_client


logger = logging.getLogger(__name__)


class SyncCancelledError(RuntimeError):
    """Raised when a sync should stop because the integration was disconnected."""


class BaseConnector(ABC):
    """Abstract base class for data source connectors.

    Subclasses should set a class-level ``meta`` attribute
    (:class:`ConnectorMeta`) that describes the connector's identity,
    capabilities, auth requirements, and available operations.
    """

    # Override in subclasses - must match our provider names
    source_system: str = "unknown"

    # Subclasses should set this to a ConnectorMeta instance.
    # Not required yet (backward compat) but needed for auto-discovery.
    meta: ConnectorMeta

    def __init__(self, organization_id: str, user_id: Optional[str] = None) -> None:
        """
        Initialize the connector.

        Args:
            organization_id: UUID of the organization to sync data for
            user_id: Optional UUID of specific user (for per-user integrations like Gmail)
        """
        self.organization_id = organization_id
        self.user_id = user_id
        self._token: Optional[str] = None
        self._credentials: Optional[dict[str, Any]] = None
        self._integration: Optional[Integration] = None

    async def ensure_sync_active(self, stage: str) -> None:
        """Stop in-flight syncs when integration has been disconnected."""
        async with get_session(organization_id=self.organization_id) as session:
            conditions = [
                Integration.organization_id == UUID(self.organization_id),
                Integration.provider == self.source_system,
            ]
            if self.user_id:
                conditions.append(Integration.user_id == UUID(self.user_id))
            else:
                conditions.append(Integration.user_id.is_(None))

            result = await session.execute(select(Integration).where(*conditions))
            integration = result.scalar_one_or_none()

        if not integration:
            logger.info(
                "Sync cancelled because integration row is missing",
                extra={
                    "organization_id": self.organization_id,
                    "provider": self.source_system,
                    "user_id": self.user_id,
                    "stage": stage,
                },
            )
            raise SyncCancelledError(
                f"{self.source_system} integration disconnected during sync ({stage})"
            )

        if not integration.is_active:
            logger.info(
                "Sync cancelled because integration was deactivated",
                extra={
                    "organization_id": self.organization_id,
                    "provider": self.source_system,
                    "integration_id": str(integration.id),
                    "user_id": self.user_id,
                    "stage": stage,
                },
            )
            raise SyncCancelledError(
                f"{self.source_system} integration deactivated during sync ({stage})"
            )

        self._integration = integration

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

    async def sync_goals(self) -> int:
        """
        Fetch and normalize goals/quotas/targets, return count synced.

        Override in subclasses that support goals (HubSpot, Salesforce).
        Default implementation returns 0.
        """
        return 0

    async def sync_all(self) -> dict[str, int]:
        """Run all sync operations.

        Returns a dict of entity-type → count-synced.  Supports both old-style
        connectors (``sync_*`` does its own DB upserts, returns ``int``) and
        new-style connectors (``sync_*`` returns a ``list`` of Pydantic record
        objects and the engine handles persistence).
        """
        await self.ensure_sync_active("sync_all:start")

        entity_order: list[str] = [
            "pipelines", "accounts", "deals", "contacts", "activities", "goals",
        ]

        result: dict[str, int] = {}
        for entity in entity_order:
            method = getattr(self, f"sync_{entity}", None)
            if method is None:
                continue

            raw = await method()
            count = await self._handle_sync_result(entity, raw)
            await self.ensure_sync_active(f"sync_all:after_{entity}")

            if count > 0 or entity in ("accounts", "deals", "contacts", "activities"):
                result[entity] = count

        return result

    async def _handle_sync_result(self, entity: str, raw: int | list[Any]) -> int:
        """Route old-style (int) vs new-style (list) sync results."""
        if isinstance(raw, int):
            return raw

        if isinstance(raw, list):
            from connectors.persistence import persist_records

            return await persist_records(
                self.organization_id, entity, raw, self.source_system,
            )

        return 0

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
        async with get_session(organization_id=self.organization_id) as session:
            # Build base query
            conditions = [
                Integration.organization_id == UUID(self.organization_id),
                Integration.provider == self.source_system,
                Integration.is_active == True,
            ]
            # Add user_id filter for per-user integrations (Gmail, Outlook)
            if self.user_id:
                conditions.append(Integration.user_id == UUID(self.user_id))
            
            result = await session.execute(
                select(Integration).where(*conditions)
            )
            integration = result.scalar_one_or_none()

            if not integration:
                user_msg = f" for user {self.user_id}" if self.user_id else ""
                raise ValueError(
                    f"No active {self.source_system} integration{user_msg} for organization: {self.organization_id}"
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

        print(f"[Connector] Getting token from Nango for {self.source_system}, connection_id={connection_id}")
        try:
            self._token = await nango.get_token(nango_integration_id, connection_id)
            print(f"[Connector] Got token for {self.source_system}: {self._token[:20]}...")
            return self._token, ""
        except Exception as e:
            print(f"[Connector] Failed to get token: {e}")
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
            async with get_session(organization_id=self.organization_id) as session:
                result = await session.execute(
                    select(Integration).where(
                        Integration.organization_id == UUID(self.organization_id),
                        Integration.provider == self.source_system,
                    )
                )
                self._integration = result.scalar_one_or_none()

        if not self._integration:
            print(f"[Sync] WARNING: No integration found for {self.source_system} in org {self.organization_id}")
            return

        async with get_session(organization_id=self.organization_id) as session:
            from sqlalchemy.orm.attributes import flag_modified
            integration = await session.get(Integration, self._integration.id)
            if integration:
                integration.last_sync_at = datetime.utcnow()
                integration.last_error = None
                if counts is not None:
                    integration.sync_stats = counts
                    # JSONB columns need explicit flag for SQLAlchemy to detect changes
                    flag_modified(integration, "sync_stats")
                    print(f"[Sync] Saving sync_stats={counts} to integration {integration.id}")
                await session.commit()
                print(f"[Sync] Committed update_last_sync for {self.source_system}")

    async def record_error(self, error: str) -> None:
        """Record an error for this integration."""
        if not self._integration:
            return

        async with get_session(organization_id=self.organization_id) as session:
            integration = await session.get(Integration, self._integration.id)
            if integration:
                integration.last_error = error[:500]  # Truncate long errors
                await session.commit()

    # ------------------------------------------------------------------
    # Capability methods – override in subclasses as needed
    # ------------------------------------------------------------------

    async def get_schema(self) -> list[dict[str, Any]]:
        """Return schema/entity metadata (QUERY capability)."""
        return []

    async def query(self, request: str) -> dict[str, Any]:
        """Execute an on-demand query (QUERY capability)."""
        raise NotImplementedError(f"{self.source_system} does not support query()")

    async def write(self, operation: str, data: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a record-level write operation (WRITE capability)."""
        raise NotImplementedError(f"{self.source_system} does not support write()")

    async def execute_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a side-effect action (ACTION capability)."""
        raise NotImplementedError(f"{self.source_system} does not support execute_action()")

    async def handle_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Handle an inbound webhook/event (LISTEN capability)."""
        raise NotImplementedError(f"{self.source_system} does not support handle_event()")

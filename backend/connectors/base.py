"""
Base connector class that all connectors inherit from.

Uses Nango for OAuth token management - tokens are fetched from Nango
on demand and automatically refreshed.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, update

from config import get_nango_integration_id
from connectors.registry import ConnectorMeta  # noqa: F401 – re-export for convenience
from models.database import get_session
from models.integration import Integration
from services.nango import get_nango_client


logger = logging.getLogger(__name__)

PENDING_SHARING_CONFIG_TIMEOUT = timedelta(minutes=30)

_CONNECTION_REMOVED_ERROR_SNIPPETS: tuple[str, ...] = (
    "connection not found",
    "404 not found",
    "404 client error",
    "invalid_auth",
    "account_inactive",
    "token_revoked",
    "not_authed",
    "auth revoked",
)

_PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "google_calendar": "Google Calendar",
    "google-calendar": "Google Calendar",
    "google_mail": "Google Mail",
    "google-mail": "Google Mail",
}


def get_provider_display_name(provider: str) -> str:
    """Return a human-readable provider label for user-facing errors."""
    normalized = provider.strip()
    if normalized in _PROVIDER_DISPLAY_NAMES:
        return _PROVIDER_DISPLAY_NAMES[normalized]
    return normalized.replace("_", " ").replace("-", " ").title()


def build_connection_removed_message(provider: str) -> str:
    """Return a coherent reconnect message when upstream access was removed."""
    provider_name = get_provider_display_name(provider)
    return (
        f"The {provider_name} connection was removed or revoked in {provider_name}. "
        f"Please disconnect it in Basebase and reconnect it if you still want to sync."
    )


def is_connection_removed_error(error: Exception | str) -> bool:
    """Return True when an error looks like a revoked or deleted upstream connection."""
    message = str(error).lower()
    return any(snippet in message for snippet in _CONNECTION_REMOVED_ERROR_SNIPPETS)


class SyncCancelledError(RuntimeError):
    """Raised when a sync should stop because the integration was disconnected."""


class ExternalConnectionRevokedError(RuntimeError):
    """Raised when the upstream integration was removed or revoked outside Basebase."""


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

    # Safety buffer subtracted from last_sync_at to avoid missing items
    # due to clock skew or eventual consistency in upstream APIs.
    _SYNC_SINCE_BUFFER: timedelta = timedelta(minutes=5)

    def __init__(
        self,
        organization_id: str,
        user_id: str | None = None,
        *,
        sync_since_override: datetime | None = None,
    ) -> None:
        """
        Initialize the connector.

        Args:
            organization_id: UUID of the organization to sync data for
            user_id: UUID of the user who owns this integration (required for all connectors)
            sync_since_override: When set (e.g. manual "resync from"), used as incremental
                cutoff instead of ``last_sync_at``. Naive UTC recommended.
        """
        self.organization_id = organization_id
        self.user_id = user_id
        self._token: str | None = None
        self._credentials: dict[str, Any] | None = None
        self._integration: Integration | None = None
        self._sync_since_override: datetime | None = sync_since_override

    @property
    def sync_since(self) -> datetime | None:
        """Return the cutoff time for incremental sync, or None for first sync.

        When ``sync_since_override`` was set at construction (manual resync), returns that.
        When a previous successful sync timestamp exists, returns that time
        minus a small safety buffer. Connectors should fall back to their
        default window (e.g. 30 days) when this returns None.
        """
        if self._sync_since_override is not None:
            return self._sync_since_override
        if self._integration and self._integration.last_sync_at:
            return self._integration.last_sync_at - self._SYNC_SINCE_BUFFER
        return None

    async def ensure_sync_active(self, stage: str) -> None:
        """Stop in-flight syncs when integration has been disconnected or pending config."""
        async with get_session(organization_id=self.organization_id) as session:
            integration = await self._select_integration(session)

            if integration and integration.pending_sharing_config:
                integration = await self._resolve_stale_pending_sharing_config(
                    session=session,
                    integration=integration,
                    stage=stage,
                )

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

        if integration.pending_sharing_config:
            logger.info(
                "Sync cancelled because sharing config is pending",
                extra={
                    "organization_id": self.organization_id,
                    "provider": self.source_system,
                    "integration_id": str(integration.id),
                    "user_id": self.user_id,
                    "stage": stage,
                },
            )
            raise SyncCancelledError(
                f"{self.source_system} integration pending sharing configuration ({stage})"
            )

        self._integration = integration

    def _activity_visibility_fields(self) -> dict[str, Any]:
        """Return integration_id, owner_user_id, visibility for Activity creation.

        Call after ensure_sync_active. Returns empty dict if no integration.
        """
        if not self._integration:
            return {}
        return {
            "integration_id": self._integration.id,
            "owner_user_id": self._integration.user_id,
            "visibility": "team" if self._integration.share_synced_data else "owner_only",
        }

    async def _resolve_stale_pending_sharing_config(
        self,
        session: Any,
        integration: Integration,
        stage: str,
    ) -> Integration:
        """Auto-resolve stale pending sharing config after timeout.

        If the integration has been stuck in ``pending_sharing_config`` for longer
        than ``PENDING_SHARING_CONFIG_TIMEOUT``, force-enable the connection by
        clearing the pending flag so syncs can proceed with existing sharing defaults.
        """
        reference_ts = integration.updated_at or integration.created_at
        if not reference_ts:
            logger.warning(
                "Integration %s has pending sharing config with no timestamps; leaving pending",
                integration.id,
                extra={
                    "organization_id": self.organization_id,
                    "provider": self.source_system,
                    "integration_id": str(integration.id),
                    "user_id": self.user_id,
                    "stage": stage,
                },
            )
            return integration

        age = datetime.utcnow() - reference_ts
        if age < PENDING_SHARING_CONFIG_TIMEOUT:
            return integration

        logger.warning(
            "Auto-resolving stale pending sharing config after timeout",
            extra={
                "organization_id": self.organization_id,
                "provider": self.source_system,
                "integration_id": str(integration.id),
                "user_id": self.user_id,
                "stage": stage,
                "pending_age_seconds": int(age.total_seconds()),
                "timeout_seconds": int(PENDING_SHARING_CONFIG_TIMEOUT.total_seconds()),
            },
        )

        result = await session.execute(
            update(Integration)
            .where(
                Integration.id == integration.id,
                Integration.pending_sharing_config == True,  # noqa: E712
            )
            .values(
                pending_sharing_config=False,
                updated_at=datetime.utcnow(),
                last_error=None,
            )
            .returning(Integration)
        )
        resolved_integration = result.scalar_one_or_none()

        await session.commit()

        if resolved_integration:
            logger.info(
                "Cleared stale pending sharing config and continuing sync",
                extra={
                    "organization_id": self.organization_id,
                    "provider": self.source_system,
                    "integration_id": str(integration.id),
                    "user_id": self.user_id,
                    "stage": stage,
                },
            )
            return resolved_integration

        refreshed_integration = await self._select_integration(session)
        if refreshed_integration:
            return refreshed_integration

        return integration

    async def check_access(
        self, operation: str, requesting_user_id: str | None
    ) -> tuple[bool, str | None]:
        """
        Check if a user has access to perform an operation via this connector.

        Args:
            operation: "sync", "query", or "write"
            requesting_user_id: UUID of the user requesting access

        Returns:
            Tuple of (allowed, deny_reason)
        """
        if not self._integration:
            await self._load_integration()

        if not self._integration:
            return False, "Integration not found"

        # Owner always has access
        if requesting_user_id and str(self._integration.user_id) == requesting_user_id:
            return True, None

        # Check sharing flags for non-owners
        if operation == "sync":
            return True, None  # Sync is always allowed (runs as owner)
        elif operation == "query":
            if self._integration.share_query_access:
                return True, None
            return False, "Query access not shared for this integration"
        elif operation == "write":
            if self._integration.share_write_access:
                return True, None
            return False, "Write access not shared for this integration"

        return False, f"Unknown operation: {operation}"

    async def _load_integration(self) -> None:
        """Load integration record from database."""
        async with get_session(organization_id=self.organization_id) as session:
            self._integration = await self._select_integration(session)

    async def _select_integration(
        self,
        session: Any,
        *,
        require_active: bool = False,
    ) -> Integration | None:
        """Return the best matching integration row for this connector context.

        When ``user_id`` is omitted, an org can have multiple user-scoped rows for a
        provider. In that case, prefer the most recently updated connection and log a
        warning to aid cleanup of ambiguous call sites.
        """
        conditions = [
            Integration.organization_id == UUID(self.organization_id),
            Integration.connector == self.source_system,
        ]
        if require_active:
            conditions.append(Integration.is_active == True)  # noqa: E712
        if self.user_id:
            conditions.append(Integration.user_id == UUID(self.user_id))

        result = await session.execute(
            select(Integration)
            .where(*conditions)
            .order_by(
                Integration.updated_at.desc().nullslast(),
                Integration.created_at.desc().nullslast(),
            )
            .limit(2)
        )
        candidates = result.scalars().all()
        if len(candidates) > 1 and not self.user_id:
            logger.warning(
                "Multiple active %s integrations found for org=%s with no user_id; using integration=%s",
                self.source_system,
                self.organization_id,
                candidates[0].id,
            )
        return candidates[0] if candidates else None

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

            kwargs: dict[str, Any] = {}
            if entity == "activities" and self._integration:
                kwargs["integration_id"] = self._integration.id
                kwargs["owner_user_id"] = self._integration.user_id
                kwargs["visibility"] = (
                    "team" if self._integration.share_synced_data else "owner_only"
                )
            return await persist_records(
                self.organization_id, entity, raw, self.source_system, **kwargs
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

        # Verify we have an active integration for this user
        async with get_session(organization_id=self.organization_id) as session:
            integration = await self._select_integration(session, require_active=True)

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
            if is_connection_removed_error(e):
                logger.warning(
                    "Upstream connection appears removed while getting token",
                    extra={
                        "organization_id": self.organization_id,
                        "provider": self.source_system,
                        "user_id": self.user_id,
                        "connection_id": connection_id,
                    },
                    exc_info=True,
                )
                raise ExternalConnectionRevokedError(
                    build_connection_removed_message(self.source_system)
                ) from e
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

        try:
            self._credentials = await nango.get_credentials(
                nango_integration_id, connection_id
            )
        except Exception as exc:
            if is_connection_removed_error(exc):
                logger.warning(
                    "Upstream connection appears removed while getting credentials",
                    extra={
                        "organization_id": self.organization_id,
                        "provider": self.source_system,
                        "user_id": self.user_id,
                        "connection_id": connection_id,
                    },
                    exc_info=True,
                )
                raise ExternalConnectionRevokedError(
                    build_connection_removed_message(self.source_system)
                ) from exc
            raise
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
                self._integration = await self._select_integration(session)

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
            await self._load_integration()
        if not self._integration:
            logger.warning(
                "record_error: no integration found for %s org=%s user=%s; error not persisted",
                self.source_system,
                self.organization_id,
                self.user_id,
            )
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

    # ------------------------------------------------------------------
    # Webhook HTTP handling (for LISTEN connectors that receive HTTP webhooks)
    # ------------------------------------------------------------------

    @staticmethod
    def verify_webhook(raw_body: bytes, headers: dict[str, str], secret: str) -> bool:
        """Verify webhook signature. Override in connectors that support LISTEN with webhooks."""
        raise NotImplementedError("Webhook verification not implemented")

    @staticmethod
    def process_webhook_payload(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """
        Parse webhook JSON and return events to emit: [(event_type, data), ...].
        Override in connectors that support LISTEN with webhooks.
        """
        return []

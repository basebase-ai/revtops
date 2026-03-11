"""
Data Protection Layer.

Interposes on connector sync and tool calls to enforce sharing permissions
and block unauthorized connector operations per org/user/context.

Org-scoped connectors (e.g. Slack, Twilio) are shared by the whole org — any
team member can use them. User-scoped connectors (e.g. Google Drive) enforce
sharing flags on Integration to control access:
- share_synced_data: Team can see synced records
- share_query_access: Team can query live data via this connection
- share_write_access: Team can write data via this connection
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select

from connectors.registry import ConnectorScope, discover_connectors
from models.database import get_session
from models.integration import Integration


@dataclass(frozen=True)
class ConnectorContext:
    """Context for connector access checks."""

    organization_id: str
    user_id: str | None
    provider: str
    operation: str  # "sync" | "query" | "write" | "action"


@dataclass(frozen=True)
class DataProtectionResult:
    """Result of a data protection check: allow/deny and optional transformed payload."""

    allowed: bool
    deny_reason: str | None = None
    transformed_payload: dict[str, Any] | None = None
    injected_credentials: dict[str, Any] | None = None
    # The integration to use (may be different from user's own)
    integration_user_id: str | None = None


async def check_connector_call(
    context: ConnectorContext,
    payload: dict[str, Any] | None = None,
) -> DataProtectionResult:
    """
    Check whether a connector call (sync, query, write, action) is allowed.

    For query/write/action operations, checks if the requesting user has access
    either through their own integration or via sharing flags on another user's integration.
    """
    # Sync operations are always allowed (they run as the integration owner)
    if context.operation == "sync":
        return DataProtectionResult(allowed=True)

    # For query/write/action, we need to check sharing permissions
    async with get_session(organization_id=context.organization_id) as session:
        # First, check if user has their own integration
        if context.user_id:
            result = await session.execute(
                select(Integration).where(
                    Integration.organization_id == UUID(context.organization_id),
                    Integration.connector == context.provider,
                    Integration.user_id == UUID(context.user_id),
                    Integration.is_active == True,  # noqa: E712
                )
            )
            own_integration = result.scalar_one_or_none()
            if own_integration:
                return DataProtectionResult(
                    allowed=True,
                    integration_user_id=context.user_id,
                )

        # Check for shared integrations
        # Org-scoped connectors: any team member can use the org's integration
        registry = discover_connectors()
        connector_cls = registry.get(context.provider)
        is_org_scoped = (
            connector_cls is not None
            and getattr(connector_cls.meta, "scope", None) == ConnectorScope.ORGANIZATION
        )

        if is_org_scoped:
            result = await session.execute(
                select(Integration).where(
                    Integration.organization_id == UUID(context.organization_id),
                    Integration.connector == context.provider,
                    Integration.is_active == True,  # noqa: E712
                )
            )
            shared_integration = result.scalar_one_or_none()
            if shared_integration:
                return DataProtectionResult(
                    allowed=True,
                    integration_user_id=str(shared_integration.user_id),
                )
        else:
            # User-scoped: check sharing flags
            share_flag_map = {
                "query": Integration.share_query_access,
                "write": Integration.share_write_access,
                "action": Integration.share_write_access,  # Actions require write access
            }
            share_flag = share_flag_map.get(context.operation)

            if share_flag is not None:
                result = await session.execute(
                    select(Integration).where(
                        Integration.organization_id == UUID(context.organization_id),
                        Integration.connector == context.provider,
                        Integration.is_active == True,  # noqa: E712
                        share_flag == True,  # noqa: E712
                    )
                )
                shared_integration = result.scalar_one_or_none()
                if shared_integration:
                    return DataProtectionResult(
                        allowed=True,
                        integration_user_id=str(shared_integration.user_id),
                    )

    # No access
    operation_name = context.operation
    if operation_name == "query":
        return DataProtectionResult(
            allowed=False,
            deny_reason=f"No {context.provider} integration with query access. Connect your own or ask a teammate to enable query sharing.",
        )
    elif operation_name in ("write", "action"):
        return DataProtectionResult(
            allowed=False,
            deny_reason=f"No {context.provider} integration with write access. Connect your own or ask a teammate to enable write sharing.",
        )

    return DataProtectionResult(
        allowed=False,
        deny_reason=f"No active {context.provider} integration found.",
    )

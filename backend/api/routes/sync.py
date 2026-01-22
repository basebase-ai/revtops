"""
Sync trigger endpoints for all integrations.

Endpoints:
- POST /api/sync/{organization_id}/{provider} - Trigger sync for specific integration
- GET /api/sync/{organization_id}/{provider}/status - Get sync status
- POST /api/sync/{organization_id}/all - Sync all active integrations
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from connectors.google_calendar import GoogleCalendarConnector
from connectors.hubspot import HubSpotConnector
from connectors.microsoft_calendar import MicrosoftCalendarConnector
from connectors.microsoft_mail import MicrosoftMailConnector
from connectors.salesforce import SalesforceConnector
from connectors.slack import SlackConnector
from models.database import get_session
from models.integration import Integration
from models.organization import Organization

router = APIRouter()

# Connector registry
CONNECTORS = {
    "salesforce": SalesforceConnector,
    "hubspot": HubSpotConnector,
    "slack": SlackConnector,
    "google_calendar": GoogleCalendarConnector,
    "microsoft_calendar": MicrosoftCalendarConnector,
    "microsoft_mail": MicrosoftMailConnector,
}

# Simple in-memory sync status tracking (use Redis in production)
_sync_status: dict[str, dict[str, str | datetime | None | dict[str, int]]] = {}


class SyncStatusResponse(BaseModel):
    """Response model for sync status."""

    organization_id: str
    provider: str
    status: str
    started_at: Optional[str]
    completed_at: Optional[str]
    error: Optional[str]
    counts: Optional[dict[str, int]]


class SyncTriggerResponse(BaseModel):
    """Response model for sync trigger."""

    status: str
    organization_id: str
    provider: str


class SyncAllResponse(BaseModel):
    """Response model for syncing all integrations."""

    status: str
    organization_id: str
    integrations: list[str]


def _get_status_key(organization_id: str, provider: str) -> str:
    """Generate a unique key for sync status tracking."""
    return f"{organization_id}:{provider}"


@router.post("/{organization_id}/{provider}", response_model=SyncTriggerResponse)
async def trigger_sync(
    organization_id: str, provider: str, background_tasks: BackgroundTasks
) -> SyncTriggerResponse:
    """Trigger a sync for a specific integration."""
    try:
        customer_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    if provider not in CONNECTORS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {provider}. Available: {list(CONNECTORS.keys())}",
        )

    # Verify integration exists and is active
    async with get_session() as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == customer_uuid,
                Integration.provider == provider,
                Integration.is_active == True,
            )
        )
        integration = result.scalar_one_or_none()

        if not integration:
            raise HTTPException(
                status_code=404,
                detail=f"No active {provider} integration found",
            )

    # Initialize sync status
    status_key = _get_status_key(organization_id, provider)
    _sync_status[status_key] = {
        "status": "syncing",
        "started_at": datetime.utcnow(),
        "completed_at": None,
        "error": None,
        "counts": None,
    }

    # Add background task
    background_tasks.add_task(sync_integration_data, organization_id, provider)

    return SyncTriggerResponse(
        status="syncing", organization_id=organization_id, provider=provider
    )


@router.get("/{organization_id}/{provider}/status", response_model=SyncStatusResponse)
async def get_sync_status(organization_id: str, provider: str) -> SyncStatusResponse:
    """Get sync status for a specific integration."""
    try:
        UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    status_key = _get_status_key(organization_id, provider)
    status = _sync_status.get(status_key)

    if not status:
        # Check database for last sync time
        async with get_session() as session:
            result = await session.execute(
                select(Integration).where(
                    Integration.organization_id == UUID(organization_id),
                    Integration.provider == provider,
                )
            )
            integration = result.scalar_one_or_none()

            if integration and integration.last_sync_at:
                return SyncStatusResponse(
                    organization_id=organization_id,
                    provider=provider,
                    status="completed",
                    started_at=None,
                    completed_at=integration.last_sync_at.isoformat(),
                    error=integration.last_error,
                    counts=None,
                )

        return SyncStatusResponse(
            organization_id=organization_id,
            provider=provider,
            status="never_synced",
            started_at=None,
            completed_at=None,
            error=None,
            counts=None,
        )

    started_at = status.get("started_at")
    completed_at = status.get("completed_at")
    counts = status.get("counts")

    return SyncStatusResponse(
        organization_id=organization_id,
        provider=provider,
        status=str(status.get("status", "unknown")),
        started_at=started_at.isoformat() if isinstance(started_at, datetime) else None,
        completed_at=completed_at.isoformat() if isinstance(completed_at, datetime) else None,
        error=str(status["error"]) if status.get("error") else None,
        counts=counts if isinstance(counts, dict) else None,
    )


@router.post("/{organization_id}/all", response_model=SyncAllResponse)
async def trigger_sync_all(
    organization_id: str, background_tasks: BackgroundTasks
) -> SyncAllResponse:
    """Trigger sync for all active integrations."""
    try:
        customer_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    # Get all active integrations
    async with get_session() as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == customer_uuid,
                Integration.is_active == True,
            )
        )
        integrations = result.scalars().all()

    if not integrations:
        raise HTTPException(status_code=404, detail="No active integrations found")

    providers = [i.provider for i in integrations]

    # Trigger sync for each integration
    for provider in providers:
        if provider in CONNECTORS:
            status_key = _get_status_key(organization_id, provider)
            _sync_status[status_key] = {
                "status": "syncing",
                "started_at": datetime.utcnow(),
                "completed_at": None,
                "error": None,
                "counts": None,
            }
            background_tasks.add_task(sync_integration_data, organization_id, provider)

    return SyncAllResponse(
        status="syncing",
        organization_id=organization_id,
        integrations=providers,
    )


async def sync_integration_data(organization_id: str, provider: str) -> None:
    """Background task to sync data for a specific integration."""
    status_key = _get_status_key(organization_id, provider)
    connector_class = CONNECTORS.get(provider)

    if not connector_class:
        _sync_status[status_key]["status"] = "failed"
        _sync_status[status_key]["error"] = f"Unknown provider: {provider}"
        _sync_status[status_key]["completed_at"] = datetime.utcnow()
        return

    try:
        connector = connector_class(organization_id)
        counts = await connector.sync_all()
        await connector.update_last_sync()

        _sync_status[status_key]["status"] = "completed"
        _sync_status[status_key]["completed_at"] = datetime.utcnow()
        _sync_status[status_key]["counts"] = counts

    except Exception as e:
        error_msg = str(e)
        _sync_status[status_key]["status"] = "failed"
        _sync_status[status_key]["error"] = error_msg
        _sync_status[status_key]["completed_at"] = datetime.utcnow()

        # Record error in database
        try:
            connector = connector_class(organization_id)
            await connector.record_error(error_msg)
        except Exception:
            pass  # Ignore errors recording the error


# Legacy endpoint for backwards compatibility
@router.post("/{organization_id}")
async def trigger_sync_legacy(
    organization_id: str, background_tasks: BackgroundTasks
) -> SyncAllResponse:
    """Legacy endpoint - triggers sync for all integrations."""
    return await trigger_sync_all(organization_id, background_tasks)

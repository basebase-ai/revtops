"""
Sync trigger endpoints for all integrations.

Endpoints:
- POST /api/sync/{organization_id}/{provider} - Trigger sync for specific integration
- GET /api/sync/{organization_id}/{provider}/status - Get sync status
- POST /api/sync/{organization_id}/all - Sync all active integrations
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from connectors.base import SyncCancelledError
from connectors.fireflies import FirefliesConnector
from connectors.gmail import GmailConnector
from connectors.google_calendar import GoogleCalendarConnector
from connectors.hubspot import HubSpotConnector
from connectors.microsoft_calendar import MicrosoftCalendarConnector
from connectors.microsoft_mail import MicrosoftMailConnector
from connectors.salesforce import SalesforceConnector
from connectors.slack import SlackConnector
from connectors.zoom import ZoomConnector
from models.database import get_session
from models.integration import Integration
from models.organization import Organization

router = APIRouter()

# Connector registry
CONNECTORS = {
    "salesforce": SalesforceConnector,
    "hubspot": HubSpotConnector,
    "slack": SlackConnector,
    "fireflies": FirefliesConnector,
    "google_calendar": GoogleCalendarConnector,
    "gmail": GmailConnector,
    "microsoft_calendar": MicrosoftCalendarConnector,
    "microsoft_mail": MicrosoftMailConnector,
    "zoom": ZoomConnector,
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


# =============================================================================
# Admin endpoints (must be defined BEFORE parameterized routes)
# =============================================================================


class GlobalSyncResponse(BaseModel):
    """Response model for global sync (all organizations)."""

    status: str
    task_id: str
    integration_count: int


class AdminIntegration(BaseModel):
    """Integration info for admin view."""

    id: str
    organization_id: str
    organization_name: str
    provider: str
    is_active: bool
    last_sync_at: str | None
    last_error: str | None
    sync_stats: dict[str, int] | None
    created_at: str | None


class AdminIntegrationsResponse(BaseModel):
    """Response model for admin integrations list."""

    integrations: list[AdminIntegration]
    total: int


@router.get("/admin/integrations", response_model=AdminIntegrationsResponse)
async def list_admin_integrations(user_id: str) -> AdminIntegrationsResponse:
    """
    List all integrations across all organizations (global admin only).
    """
    from models.database import get_admin_session
    from models.user import User
    
    # Verify user is global admin
    async with get_admin_session() as session:
        result = await session.execute(
            select(User).where(User.id == UUID(user_id))
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if "global_admin" not in (user.roles or []):
            raise HTTPException(status_code=403, detail="Requires global_admin role")
        
        # Get all integrations with org names
        result = await session.execute(
            select(Integration, Organization.name.label("org_name"))
            .join(Organization, Integration.organization_id == Organization.id)
            .order_by(Organization.name, Integration.provider)
        )
        rows = result.all()
    
    integrations: list[AdminIntegration] = []
    for row in rows:
        integration = row[0]
        org_name = row[1]
        integrations.append(AdminIntegration(
            id=str(integration.id),
            organization_id=str(integration.organization_id),
            organization_name=org_name,
            provider=integration.provider,
            is_active=integration.is_active,
            last_sync_at=integration.last_sync_at.isoformat() if integration.last_sync_at else None,
            last_error=integration.last_error,
            sync_stats=integration.sync_stats,
            created_at=integration.created_at.isoformat() if integration.created_at else None,
        ))
    
    return AdminIntegrationsResponse(
        integrations=integrations,
        total=len(integrations),
    )


@router.post("/admin/all", response_model=GlobalSyncResponse)
async def trigger_global_sync(user_id: str) -> GlobalSyncResponse:
    """
    Trigger sync for ALL organizations (global admin only).
    
    This calls the same task that the hourly beat scheduler runs.
    """
    from models.database import get_admin_session
    from models.user import User
    
    # Verify user is global admin
    async with get_admin_session() as session:
        result = await session.execute(
            select(User).where(User.id == UUID(user_id))
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if "global_admin" not in (user.roles or []):
            raise HTTPException(status_code=403, detail="Requires global_admin role")
        
        # Count active integrations
        result = await session.execute(
            select(Integration).where(Integration.is_active == True)
        )
        integrations = result.scalars().all()
    
    if not integrations:
        raise HTTPException(status_code=404, detail="No active integrations found")
    
    # Queue the global sync task (same as hourly beat task)
    from workers.tasks.sync import sync_all_organizations
    task = sync_all_organizations.delay()
    
    return GlobalSyncResponse(
        status="queued",
        task_id=task.id,
        integration_count=len(integrations),
    )


# =============================================================================
# Organization-scoped endpoints
# =============================================================================


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
    async with get_session(organization_id=organization_id) as session:
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
        async with get_session(organization_id=organization_id) as session:
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
                    completed_at=f"{integration.last_sync_at.isoformat()}Z",
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
        started_at=f"{started_at.isoformat()}Z" if isinstance(started_at, datetime) else None,
        completed_at=f"{completed_at.isoformat()}Z" if isinstance(completed_at, datetime) else None,
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
    async with get_session(organization_id=organization_id) as session:
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
        print(f"[Sync] Starting sync for {provider} in org {organization_id}")
        connector = connector_class(organization_id)
        counts = await connector.sync_all()
        print(f"[Sync] sync_all returned counts: {counts}")
        await connector.update_last_sync(counts)
        print(f"[Sync] Completed sync for {provider}, saved sync_stats: {counts}")

        _sync_status[status_key]["status"] = "completed"
        _sync_status[status_key]["completed_at"] = datetime.utcnow()
        _sync_status[status_key]["counts"] = counts

        # Generate embeddings for newly synced activities (non-blocking)
        try:
            from services.embedding_sync import generate_embeddings_for_organization
            embedded_count = await generate_embeddings_for_organization(
                organization_id, limit=500  # Limit per sync to avoid timeout
            )
            if embedded_count > 0:
                print(f"Generated embeddings for {embedded_count} activities")
        except Exception as embed_err:
            # Don't fail sync if embedding generation fails
            print(f"Warning: Embedding generation failed: {embed_err}")

        # Emit sync completed event for workflow triggers
        try:
            from workers.events import emit_event
            await emit_event(
                event_type="sync.completed",
                organization_id=organization_id,
                data={
                    "provider": provider,
                    "counts": counts,
                    "completed_at": datetime.utcnow().isoformat(),
                },
            )
        except Exception as event_err:
            print(f"Warning: Event emission failed: {event_err}")

    except SyncCancelledError as e:
        cancel_msg = str(e)
        print(f"[Sync] CANCELLED syncing {provider} for org {organization_id}: {cancel_msg}")

        _sync_status[status_key]["status"] = "cancelled"
        _sync_status[status_key]["error"] = cancel_msg
        _sync_status[status_key]["completed_at"] = datetime.utcnow()

        # Emit sync cancelled event (best effort)
        try:
            from workers.events import emit_event
            await emit_event(
                event_type="sync.cancelled",
                organization_id=organization_id,
                data={
                    "provider": provider,
                    "message": cancel_msg,
                    "cancelled_at": datetime.utcnow().isoformat(),
                },
            )
        except Exception:
            pass

    except Exception as e:
        import traceback
        error_msg = str(e)
        full_traceback = traceback.format_exc()
        print(f"[Sync] ERROR syncing {provider} for org {organization_id}: {error_msg}")
        print(f"[Sync] Traceback:\n{full_traceback}")
        
        _sync_status[status_key]["status"] = "failed"
        _sync_status[status_key]["error"] = error_msg
        _sync_status[status_key]["completed_at"] = datetime.utcnow()

        # Record error in database
        try:
            connector = connector_class(organization_id)
            await connector.record_error(error_msg)
        except Exception as record_err:
            print(f"[Sync] Failed to record error to DB: {record_err}")

        # Emit sync failed event
        try:
            from workers.events import emit_event
            await emit_event(
                event_type="sync.failed",
                organization_id=organization_id,
                data={
                    "provider": provider,
                    "error": error_msg,
                    "failed_at": datetime.utcnow().isoformat(),
                },
            )
        except Exception:
            pass


class OwnerMatchResult(BaseModel):
    """Single owner match result."""

    email: str
    hubspot_owner_id: str
    user_id: str | None
    user_name: str | None
    matched: bool


class OwnerMatchResponse(BaseModel):
    """Response model for HubSpot owner matching."""

    matched: int
    unmatched: int
    results: list[OwnerMatchResult]


@router.post(
    "/{organization_id}/hubspot/match-owners",
    response_model=OwnerMatchResponse,
)
async def match_hubspot_owners(organization_id: str) -> OwnerMatchResponse:
    """
    Fetch all HubSpot owners and match them to local users by email.

    Populates ``users.hubspot_user_id`` for every match found.
    """
    try:
        UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    # Verify HubSpot integration is active
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.provider == "hubspot",
                Integration.is_active == True,
            )
        )
        integration: Integration | None = result.scalar_one_or_none()
        if not integration:
            raise HTTPException(
                status_code=404, detail="No active HubSpot integration found"
            )

    connector: HubSpotConnector = HubSpotConnector(organization_id)
    raw_results: list[dict[str, Any]] = await connector.match_owners_to_users()

    results: list[OwnerMatchResult] = [
        OwnerMatchResult(
            email=r["email"],
            hubspot_owner_id=r["hubspot_owner_id"],
            user_id=r.get("user_id"),
            user_name=r.get("user_name"),
            matched=r["matched"],
        )
        for r in raw_results
    ]
    matched_count: int = sum(1 for r in results if r.matched)
    unmatched_count: int = len(results) - matched_count

    return OwnerMatchResponse(
        matched=matched_count,
        unmatched=unmatched_count,
        results=results,
    )


# Legacy endpoint for backwards compatibility
@router.post("/{organization_id}")
async def trigger_sync_legacy(
    organization_id: str, background_tasks: BackgroundTasks
) -> SyncAllResponse:
    """Legacy endpoint - triggers sync for all integrations."""
    return await trigger_sync_all(organization_id, background_tasks)


# ============================================================================
# Celery-based sync endpoints (queued execution)
# ============================================================================


class QueuedSyncResponse(BaseModel):
    """Response model for queued sync."""

    status: str
    task_id: str
    organization_id: str
    provider: Optional[str] = None


@router.post("/{organization_id}/{provider}/queue", response_model=QueuedSyncResponse)
async def queue_sync(organization_id: str, provider: str) -> QueuedSyncResponse:
    """
    Queue a sync for execution via Celery worker.
    
    This is the preferred method for scheduled syncs as it:
    - Handles retries automatically
    - Doesn't block the API server
    - Can be monitored via task_id
    """
    try:
        customer_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    if provider not in CONNECTORS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {provider}. Available: {list(CONNECTORS.keys())}",
        )

    # Verify integration exists
    async with get_session(organization_id=organization_id) as session:
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

    # Queue task via Celery
    from workers.tasks.sync import sync_integration
    task = sync_integration.delay(organization_id, provider)

    return QueuedSyncResponse(
        status="queued",
        task_id=task.id,
        organization_id=organization_id,
        provider=provider,
    )


@router.post("/{organization_id}/all/queue", response_model=QueuedSyncResponse)
async def queue_sync_all(organization_id: str) -> QueuedSyncResponse:
    """Queue sync for all integrations via Celery worker."""
    try:
        customer_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    # Verify org has integrations
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == customer_uuid,
                Integration.is_active == True,
            )
        )
        integrations = result.scalars().all()

    if not integrations:
        raise HTTPException(status_code=404, detail="No active integrations found")

    # Queue task via Celery
    from workers.tasks.sync import sync_organization
    task = sync_organization.delay(organization_id)

    return QueuedSyncResponse(
        status="queued",
        task_id=task.id,
        organization_id=organization_id,
    )

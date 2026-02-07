"""
Sync tasks for Celery workers.

These tasks handle syncing data from external integrations (CRM, calendar, etc.)
on a scheduled or on-demand basis.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend directory is in Python path for Celery forked workers
_backend_dir = Path(__file__).resolve().parent.parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

import asyncio
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def run_async(coro: Any) -> Any:
    """Run an async function in a sync context (for Celery tasks).
    
    Creates a fresh event loop and disposes any existing database connections
    to avoid 'Future attached to different loop' errors with asyncpg.
    """
    from models.database import dispose_engine
    
    # Dispose existing connections - they're tied to a previous (closed) event loop
    # and will cause "Future attached to different loop" errors if reused
    dispose_engine()
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _sync_integration(organization_id: str, provider: str) -> dict[str, Any]:
    """
    Internal async function to sync a single integration.
    
    Returns sync results including counts and any errors.
    """
    from connectors.fireflies import FirefliesConnector
    from connectors.gmail import GmailConnector
    from connectors.google_calendar import GoogleCalendarConnector
    from connectors.hubspot import HubSpotConnector
    from connectors.microsoft_calendar import MicrosoftCalendarConnector
    from connectors.microsoft_mail import MicrosoftMailConnector
    from connectors.salesforce import SalesforceConnector
    from connectors.base import SyncCancelledError
    from connectors.slack import SlackConnector
    from services.embedding_sync import generate_embeddings_for_organization
    from workers.events import emit_event

    connectors: dict[str, type] = {
        "salesforce": SalesforceConnector,
        "hubspot": HubSpotConnector,
        "slack": SlackConnector,
        "fireflies": FirefliesConnector,
        "google_calendar": GoogleCalendarConnector,
        "gmail": GmailConnector,
        "microsoft_calendar": MicrosoftCalendarConnector,
        "microsoft_mail": MicrosoftMailConnector,
    }

    connector_class = connectors.get(provider)
    if not connector_class:
        return {
            "status": "failed",
            "error": f"Unknown provider: {provider}",
            "organization_id": organization_id,
            "provider": provider,
        }

    try:
        logger.info(f"Starting sync for {provider} in org {organization_id}")
        connector = connector_class(organization_id)
        counts = await connector.sync_all()
        await connector.update_last_sync(counts)

        # Generate embeddings for newly synced activities
        try:
            embedded_count = await generate_embeddings_for_organization(
                organization_id, limit=500
            )
            logger.info(f"Generated embeddings for {embedded_count} activities")
        except Exception as embed_err:
            logger.warning(f"Embedding generation failed: {embed_err}")

        # Emit sync completed event for workflow triggers
        await emit_event(
            event_type="sync.completed",
            organization_id=organization_id,
            data={
                "provider": provider,
                "counts": counts,
                "completed_at": datetime.utcnow().isoformat(),
            },
        )

        logger.info(f"Completed sync for {provider} in org {organization_id}: {counts}")
        return {
            "status": "completed",
            "organization_id": organization_id,
            "provider": provider,
            "counts": counts,
        }

    except SyncCancelledError as e:
        cancel_msg = str(e)
        logger.info(f"Sync cancelled for {provider} in org {organization_id}: {cancel_msg}")
        return {
            "status": "cancelled",
            "organization_id": organization_id,
            "provider": provider,
            "error": cancel_msg,
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Sync failed for {provider} in org {organization_id}: {error_msg}")

        # Record error in database
        try:
            connector = connector_class(organization_id)
            await connector.record_error(error_msg)
        except Exception:
            pass

        # Emit sync failed event
        await emit_event(
            event_type="sync.failed",
            organization_id=organization_id,
            data={
                "provider": provider,
                "error": error_msg,
                "failed_at": datetime.utcnow().isoformat(),
            },
        )

        return {
            "status": "failed",
            "organization_id": organization_id,
            "provider": provider,
            "error": error_msg,
        }


async def _get_all_active_integrations() -> list[dict[str, str]]:
    """Get all active integrations across all organizations."""
    from sqlalchemy import select
    from models.database import get_admin_session
    from models.integration import Integration

    # Use admin session to bypass RLS and query across all organizations
    async with get_admin_session() as session:
        result = await session.execute(
            select(Integration).where(Integration.is_active == True)
        )
        integrations = result.scalars().all()
        
        return [
            {
                "organization_id": str(i.organization_id),
                "provider": i.provider,
            }
            for i in integrations
        ]


async def _get_org_integrations(organization_id: str) -> list[str]:
    """Get all active integration providers for an organization."""
    from sqlalchemy import select
    from models.database import get_session
    from models.integration import Integration

    async with get_session() as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.is_active == True,
            )
        )
        integrations = result.scalars().all()
        return [i.provider for i in integrations]


@celery_app.task(bind=True, name="workers.tasks.sync.sync_integration")
def sync_integration(
    self: Any, organization_id: str, provider: str
) -> dict[str, Any]:
    """
    Celery task to sync a single integration.
    
    Args:
        organization_id: UUID of the organization
        provider: Integration provider name (e.g., 'hubspot', 'salesforce')
    
    Returns:
        Dict with sync status, counts, and any errors
    """
    logger.info(f"Task {self.request.id}: Syncing {provider} for org {organization_id}")
    return run_async(_sync_integration(organization_id, provider))


@celery_app.task(bind=True, name="workers.tasks.sync.sync_organization")
def sync_organization(self: Any, organization_id: str) -> dict[str, Any]:
    """
    Celery task to sync all integrations for a single organization.
    
    Args:
        organization_id: UUID of the organization
    
    Returns:
        Dict with results for each integration
    """
    logger.info(f"Task {self.request.id}: Syncing all integrations for org {organization_id}")
    
    async def _sync_all_for_org() -> dict[str, Any]:
        providers = await _get_org_integrations(organization_id)
        results: dict[str, Any] = {}
        
        for provider in providers:
            results[provider] = await _sync_integration(organization_id, provider)
        
        return {
            "organization_id": organization_id,
            "integrations": results,
            "completed_at": datetime.utcnow().isoformat(),
        }
    
    return run_async(_sync_all_for_org())


@celery_app.task(bind=True, name="workers.tasks.sync.sync_all_organizations")
def sync_all_organizations(self: Any) -> dict[str, Any]:
    """
    Celery task to sync all integrations for all organizations.
    
    This is the hourly sync task that runs via Beat schedule.
    
    Returns:
        Dict with summary of all sync operations
    """
    logger.info(f"Task {self.request.id}: Starting hourly sync for all organizations")
    
    async def _sync_all() -> dict[str, Any]:
        integrations = await _get_all_active_integrations()
        
        # Group by organization
        orgs: dict[str, list[str]] = {}
        for integration in integrations:
            org_id = integration["organization_id"]
            if org_id not in orgs:
                orgs[org_id] = []
            orgs[org_id].append(integration["provider"])
        
        results: dict[str, dict[str, Any]] = {}
        total_synced = 0
        total_failed = 0
        
        for org_id, providers in orgs.items():
            results[org_id] = {}
            for provider in providers:
                result = await _sync_integration(org_id, provider)
                results[org_id][provider] = result
                if result["status"] == "completed":
                    total_synced += 1
                else:
                    total_failed += 1
        
        summary = {
            "total_organizations": len(orgs),
            "total_integrations_synced": total_synced,
            "total_integrations_failed": total_failed,
            "started_at": datetime.utcnow().isoformat(),
            "results": results,
        }
        
        logger.info(
            f"Hourly sync complete: {total_synced} succeeded, {total_failed} failed"
        )
        return summary
    
    return run_async(_sync_all())

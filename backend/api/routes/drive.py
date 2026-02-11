"""
Google Drive routes.

Endpoints for syncing Drive metadata, searching files, and reading file content.
"""

import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from connectors.google_drive import GoogleDriveConnector
from models.database import get_session
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Request / Response Models
# =============================================================================


class DriveSyncResponse(BaseModel):
    """Response for a sync trigger."""
    status: str
    message: str


class DriveFileInfo(BaseModel):
    """File metadata returned from search."""
    external_id: str
    source: str
    name: str
    mime_type: str
    folder_path: str
    web_view_link: Optional[str]
    file_size: Optional[int]
    source_modified_at: Optional[str]
    synced_at: Optional[str]


class DriveSearchResponse(BaseModel):
    """Response for file search."""
    files: list[DriveFileInfo]
    count: int


class DriveFileContentResponse(BaseModel):
    """Response for file content read."""
    file_name: str
    external_id: str
    mime_type: str
    folder_path: str
    web_view_link: Optional[str]
    content: str
    truncated: bool
    content_length: int


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/sync", response_model=DriveSyncResponse)
async def sync_drive(
    background_tasks: BackgroundTasks,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> DriveSyncResponse:
    """
    Trigger a full metadata sync of the user's Google Drive.

    Runs in the background. File metadata is stored in the database
    so the agent can search without hitting Google's API.
    """
    org_id, usr_id = await _get_org_and_user(user_id, organization_id)

    background_tasks.add_task(_run_sync, org_id=org_id, user_id=usr_id)

    return DriveSyncResponse(
        status="syncing",
        message="Google Drive sync started. This may take a minute for large drives.",
    )


@router.get("/search", response_model=DriveSearchResponse)
async def search_files(
    q: str,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    limit: int = 20,
) -> DriveSearchResponse:
    """
    Search synced Drive files by name (case-insensitive substring match).

    Requires a prior sync to have been completed.
    """
    org_id, usr_id = await _get_org_and_user(user_id, organization_id)

    try:
        connector = GoogleDriveConnector(org_id, usr_id)
        files: list[dict[str, Any]] = await connector.search_files(q, limit=limit)
        return DriveSearchResponse(
            files=[DriveFileInfo(**f) for f in files],
            count=len(files),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("[Drive] Search failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@router.get("/files/{external_id}/content", response_model=DriveFileContentResponse)
async def read_file_content(
    external_id: str,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> DriveFileContentResponse:
    """
    Read the text content of a Google Drive file.

    Google Docs → plain text, Sheets → CSV, Slides → plain text.
    Other text-based files are downloaded directly.
    """
    org_id, usr_id = await _get_org_and_user(user_id, organization_id)

    try:
        connector = GoogleDriveConnector(org_id, usr_id)
        result: dict[str, Any] = await connector.get_file_content(external_id)

        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])

        return DriveFileContentResponse(**result)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("[Drive] Read content failed for %s: %s", external_id, e)
        raise HTTPException(
            status_code=500, detail=f"Failed to read file: {str(e)}"
        )


# =============================================================================
# Helpers
# =============================================================================


async def _get_org_and_user(
    user_id: Optional[str],
    organization_id: Optional[str],
) -> tuple[str, str]:
    """Resolve org and user IDs from request params."""
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    org_id: str = ""

    if organization_id:
        org_id = organization_id
    else:
        async with get_session() as session:
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid user ID")

            user = await session.get(User, user_uuid)
            if not user or not user.organization_id:
                raise HTTPException(status_code=404, detail="User not found")
            org_id = str(user.organization_id)

    return org_id, user_id


async def _run_sync(org_id: str, user_id: str) -> None:
    """Background task to sync Drive metadata."""
    try:
        connector = GoogleDriveConnector(org_id, user_id)
        counts: dict[str, int] = await connector.sync_file_metadata()
        total: int = sum(counts.values())

        # Update integration sync stats
        async with get_session(organization_id=org_id) as session:
            from sqlalchemy import select as sa_select
            from models.integration import Integration

            result = await session.execute(
                sa_select(Integration).where(
                    Integration.organization_id == UUID(org_id),
                    Integration.provider == "google_drive",
                    Integration.user_id == UUID(user_id),
                )
            )
            integration = result.scalar_one_or_none()
            if integration:
                integration.last_sync_at = datetime.utcnow()
                integration.sync_stats = {"total_files": total, **counts}
                await session.commit()

        logger.info("[Drive] Sync complete for org=%s user=%s: %d files", org_id, user_id, total)
    except Exception as e:
        logger.error("[Drive] Sync failed for org=%s user=%s: %s", org_id, user_id, e)

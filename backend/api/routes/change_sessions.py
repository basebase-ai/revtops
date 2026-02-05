"""
API routes for change sessions (local-first CRM changes).

Provides endpoints to:
- List pending change sessions
- Commit changes to external CRM
- Discard/undo local changes
"""
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from models.change_session import ChangeSession
from models.record_snapshot import RecordSnapshot
from models.user import User
from models.database import get_session

router = APIRouter(prefix="/change-sessions", tags=["change-sessions"])


async def _get_user(user_id: Optional[str]) -> User:
    """Get and validate user from user_id query parameter."""
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    
    async with get_session() as session:
        user = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user


class ChangeSessionSummary(BaseModel):
    """Summary of a change session for the pending changes bar."""
    id: str
    status: str
    description: str | None
    created_at: str
    record_count: int
    records: list[dict[str, Any]]


class PendingChangesResponse(BaseModel):
    """Response for pending changes endpoint."""
    pending_count: int
    sessions: list[ChangeSessionSummary]


class CommitRequest(BaseModel):
    """Request to commit a change session."""
    pass  # No extra fields needed


class DiscardRequest(BaseModel):
    """Request to discard a change session."""
    pass  # No extra fields needed


class ActionResponse(BaseModel):
    """Response for commit/discard actions."""
    status: str
    message: str
    synced_count: int | None = None
    deleted_count: int | None = None
    error_count: int | None = None
    errors: list[dict[str, Any]] | None = None


@router.get("/pending", response_model=PendingChangesResponse)
async def get_pending_changes(
    user_id: Optional[str] = Query(None),
) -> PendingChangesResponse:
    """
    Get all pending change sessions for the current organization.
    
    Returns a summary of pending local changes that can be committed or discarded.
    """
    user = await _get_user(user_id)
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="User not associated with an organization")
    
    async with get_session() as session:
        # Get all pending change sessions for this org
        result = await session.execute(
            select(ChangeSession)
            .where(
                ChangeSession.organization_id == user.organization_id,
                ChangeSession.status == "pending",
            )
            .order_by(ChangeSession.created_at.desc())
        )
        change_sessions = result.scalars().all()
        
        sessions_summary: list[ChangeSessionSummary] = []
        
        for cs in change_sessions:
            # Get snapshots for this session
            snap_result = await session.execute(
                select(RecordSnapshot)
                .where(RecordSnapshot.change_session_id == cs.id)
            )
            snapshots = snap_result.scalars().all()
            
            records: list[dict[str, Any]] = []
            for snap in snapshots:
                record_info: dict[str, Any] = {
                    "table": snap.table_name,
                    "operation": snap.operation,
                    "record_id": str(snap.record_id),
                }
                
                # Add summary info from after_data
                if snap.after_data and isinstance(snap.after_data, dict):
                    if snap.table_name == "contacts":
                        record_info["name"] = snap.after_data.get("name")
                        record_info["email"] = snap.after_data.get("email")
                    elif snap.table_name == "accounts":
                        record_info["name"] = snap.after_data.get("name")
                        record_info["domain"] = snap.after_data.get("domain")
                    elif snap.table_name == "deals":
                        record_info["name"] = snap.after_data.get("name")
                        record_info["amount"] = snap.after_data.get("amount")
                
                records.append(record_info)
            
            sessions_summary.append(ChangeSessionSummary(
                id=str(cs.id),
                status=cs.status,
                description=cs.description,
                created_at=cs.created_at.isoformat() if cs.created_at else "",
                record_count=len(snapshots),
                records=records,
            ))
    
    return PendingChangesResponse(
        pending_count=len(sessions_summary),
        sessions=sessions_summary,
    )


@router.post("/{session_id}/commit", response_model=ActionResponse)
async def commit_change_session(
    session_id: str,
    user_id: Optional[str] = Query(None),
) -> ActionResponse:
    """
    Commit a pending change session - push local records to external CRM.
    """
    from agents.tools import commit_change_session as do_commit
    
    user = await _get_user(user_id)
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="User not associated with an organization")
    
    # Verify the session belongs to this org
    async with get_session() as session:
        cs = await session.get(ChangeSession, UUID(session_id))
        if not cs:
            raise HTTPException(status_code=404, detail="Change session not found")
        if cs.organization_id != user.organization_id:
            raise HTTPException(status_code=403, detail="Access denied")
    
    result = await do_commit(session_id, str(user.id))
    
    return ActionResponse(
        status=result.get("status", "unknown"),
        message=result.get("message", ""),
        synced_count=result.get("synced_count"),
        error_count=result.get("error_count"),
        errors=result.get("errors"),
    )


@router.post("/{session_id}/discard", response_model=ActionResponse)
async def discard_change_session(
    session_id: str,
    user_id: Optional[str] = Query(None),
) -> ActionResponse:
    """
    Discard a pending change session - delete local pending records.
    """
    from agents.tools import discard_change_session as do_discard
    
    user = await _get_user(user_id)
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="User not associated with an organization")
    
    # Verify the session belongs to this org
    async with get_session() as session:
        cs = await session.get(ChangeSession, UUID(session_id))
        if not cs:
            raise HTTPException(status_code=404, detail="Change session not found")
        if cs.organization_id != user.organization_id:
            raise HTTPException(status_code=403, detail="Access denied")
    
    result = await do_discard(session_id, str(user.id))
    
    return ActionResponse(
        status=result.get("status", "unknown"),
        message=result.get("message", ""),
        deleted_count=result.get("deleted_count"),
    )


@router.post("/commit-all", response_model=ActionResponse)
async def commit_all_pending(
    user_id: Optional[str] = Query(None),
) -> ActionResponse:
    """
    Commit all pending change sessions for the current organization.
    """
    from agents.tools import commit_change_session as do_commit
    
    user = await _get_user(user_id)
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="User not associated with an organization")
    
    async with get_session() as session:
        result = await session.execute(
            select(ChangeSession)
            .where(
                ChangeSession.organization_id == user.organization_id,
                ChangeSession.status == "pending",
            )
        )
        pending_sessions = result.scalars().all()
    
    total_synced = 0
    total_errors = 0
    all_errors: list[dict[str, Any]] = []
    
    for cs in pending_sessions:
        commit_result = await do_commit(str(cs.id), str(user.id))
        total_synced += commit_result.get("synced_count", 0)
        total_errors += commit_result.get("error_count", 0)
        if commit_result.get("errors"):
            all_errors.extend(commit_result["errors"])
    
    return ActionResponse(
        status="completed" if total_errors == 0 else "partial",
        message=f"Committed {len(pending_sessions)} session(s), synced {total_synced} record(s)",
        synced_count=total_synced,
        error_count=total_errors,
        errors=all_errors if all_errors else None,
    )


@router.post("/discard-all", response_model=ActionResponse)
async def discard_all_pending(
    user_id: Optional[str] = Query(None),
) -> ActionResponse:
    """
    Discard all pending change sessions for the current organization.
    """
    from agents.tools import discard_change_session as do_discard
    
    user = await _get_user(user_id)
    if not user.organization_id:
        raise HTTPException(status_code=400, detail="User not associated with an organization")
    
    async with get_session() as session:
        result = await session.execute(
            select(ChangeSession)
            .where(
                ChangeSession.organization_id == user.organization_id,
                ChangeSession.status == "pending",
            )
        )
        pending_sessions = result.scalars().all()
    
    total_deleted = 0
    
    for cs in pending_sessions:
        discard_result = await do_discard(str(cs.id), str(user.id))
        total_deleted += discard_result.get("deleted_count", 0)
    
    return ActionResponse(
        status="discarded",
        message=f"Discarded {len(pending_sessions)} session(s), deleted {total_deleted} record(s)",
        deleted_count=total_deleted,
    )

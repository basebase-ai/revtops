"""
API routes for change sessions (local-first CRM changes).

Provides endpoints to:
- List pending change sessions
- Commit changes to external CRM
- Discard/undo local changes
"""
import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from models.change_session import ChangeSession
from models.conversation import Conversation
from models.record_snapshot import RecordSnapshot
from models.user import User

from models.workflow import Workflow
from models.database import get_admin_session, get_session


router = APIRouter(prefix="/change-sessions", tags=["change-sessions"])
logger = logging.getLogger(__name__)


async def _get_user(user_id: Optional[str]) -> User:
    """Get and validate user from user_id query parameter."""
    if not user_id:
        logger.warning("[change_sessions] Missing user_id query parameter")
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        logger.warning("[change_sessions] Invalid user_id provided: %s", user_id)
        raise HTTPException(status_code=400, detail="Invalid user ID")
    
    logger.debug("[change_sessions] Looking up user %s with admin session", user_id)
    async with get_admin_session() as session:
        user = await session.get(User, user_uuid)
        if not user:
            logger.warning("[change_sessions] User not found: %s", user_id)
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
    conversation_id: str | None = None
    source_title: str | None = None
    source_type: str | None = None  # 'workflow' | 'chat'


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
        logger.warning("[change_sessions] User %s has no organization", user.id)
        raise HTTPException(status_code=400, detail="User not associated with an organization")
    
    logger.debug(
        "[change_sessions] Fetching pending change sessions for org %s",
        user.organization_id,
    )
    async with get_session(str(user.organization_id)) as session:
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
        logger.info(
            "[change_sessions] Found %d pending change session(s) for org %s",
            len(change_sessions),
            user.organization_id,
        )
        
        sessions_summary: list[ChangeSessionSummary] = []
        
        for cs in change_sessions:
            # Get snapshots for this session
            snap_result = await session.execute(
                select(RecordSnapshot)
                .where(RecordSnapshot.change_session_id == cs.id)
            )
            snapshots = snap_result.scalars().all()
            logger.debug(
                "[change_sessions] Change session %s has %d snapshots",
                cs.id,
                len(snapshots),
            )
            
            records: list[dict[str, Any]] = []
            for snap in snapshots:
                record_info: dict[str, Any] = {
                    "table": snap.table_name,
                    "operation": snap.operation,
                    "record_id": str(snap.record_id),
                }
                
                # Summary from after_data._input; for updates fall back to before_data for display
                input_data: dict[str, Any] = {}
                if snap.after_data and isinstance(snap.after_data, dict):
                    input_data = snap.after_data.get("_input", snap.after_data)
                before: dict[str, Any] = snap.before_data if isinstance(snap.before_data, dict) else {}

                if snap.table_name == "contacts":
                    fn: str = input_data.get("firstname") or ""
                    ln: str = input_data.get("lastname") or ""
                    display_name: str | None = f"{fn} {ln}".strip() or input_data.get("email")
                    # For updates the _input only has changed fields; use before_data for name
                    if not display_name and before:
                        display_name = before.get("name") or before.get("email")
                    record_info["name"] = display_name
                    record_info["email"] = input_data.get("email") or before.get("email")
                elif snap.table_name == "accounts":
                    record_info["name"] = input_data.get("name") or before.get("name")
                    record_info["domain"] = input_data.get("domain") or before.get("domain")
                elif snap.table_name == "deals":
                    record_info["name"] = input_data.get("dealname") or input_data.get("name") or before.get("name")
                    record_info["amount"] = input_data.get("amount") or before.get("amount")

                # For updates, show what's changing
                if snap.operation == "update" and input_data:
                    changes: list[str] = [f"{k} → {v}" for k, v in input_data.items() if k != "id"]
                    record_info["changes"] = changes
                
                records.append(record_info)

            # Optional: source conversation and workflow for "From: Chat / Workflow" in UI
            conversation_id_str: str | None = str(cs.conversation_id) if cs.conversation_id else None
            source_title: str | None = None
            source_type: str | None = None
            if cs.conversation_id:
                conv = await session.get(Conversation, cs.conversation_id)
                if conv:
                    if conv.workflow_id:
                        wf = await session.get(Workflow, conv.workflow_id)
                        source_title = wf.name if wf else conv.title
                        source_type = "workflow"
                    else:
                        source_title = conv.title
                        source_type = "chat"

            sessions_summary.append(ChangeSessionSummary(
                id=str(cs.id),
                status=cs.status,
                description=cs.description,
                created_at=cs.created_at.isoformat() if cs.created_at else "",
                record_count=len(snapshots),
                records=records,
                conversation_id=conversation_id_str,
                source_title=source_title,
                source_type=source_type,
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
        logger.warning("[change_sessions] User %s has no organization", user.id)
        raise HTTPException(status_code=400, detail="User not associated with an organization")
    
    # Verify the session belongs to this org
    async with get_session(str(user.organization_id)) as session:
        cs = await session.get(ChangeSession, UUID(session_id))
        if not cs:
            logger.warning("[change_sessions] Change session not found: %s", session_id)
            raise HTTPException(status_code=404, detail="Change session not found")
        if cs.organization_id != user.organization_id:
            logger.warning(
                "[change_sessions] Access denied for session %s (org mismatch)",
                session_id,
            )
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
        logger.warning("[change_sessions] User %s has no organization", user.id)
        raise HTTPException(status_code=400, detail="User not associated with an organization")
    
    # Verify the session belongs to this org
    async with get_session(str(user.organization_id)) as session:
        cs = await session.get(ChangeSession, UUID(session_id))
        if not cs:
            logger.warning("[change_sessions] Change session not found: %s", session_id)
            raise HTTPException(status_code=404, detail="Change session not found")
        if cs.organization_id != user.organization_id:
            logger.warning(
                "[change_sessions] Access denied for session %s (org mismatch)",
                session_id,
            )
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
        logger.warning("[change_sessions] User %s has no organization", user.id)
        raise HTTPException(status_code=400, detail="User not associated with an organization")
    
    async with get_session(str(user.organization_id)) as session:
        result = await session.execute(
            select(ChangeSession)
            .where(
                ChangeSession.organization_id == user.organization_id,
                ChangeSession.status == "pending",
            )
        )
        pending_sessions = result.scalars().all()
        logger.info(
            "[change_sessions] Committing %d pending session(s) for org %s",
            len(pending_sessions),
            user.organization_id,
        )
    
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
        logger.warning("[change_sessions] User %s has no organization", user.id)
        raise HTTPException(status_code=400, detail="User not associated with an organization")
    
    async with get_session(str(user.organization_id)) as session:
        result = await session.execute(
            select(ChangeSession)
            .where(
                ChangeSession.organization_id == user.organization_id,
                ChangeSession.status == "pending",
            )
        )
        pending_sessions = result.scalars().all()
        logger.info(
            "[change_sessions] Discarding %d pending session(s) for org %s",
            len(pending_sessions),
            user.organization_id,
        )
    
    total_deleted = 0
    
    for cs in pending_sessions:
        discard_result = await do_discard(str(cs.id), str(user.id))
        total_deleted += discard_result.get("deleted_count", 0)
    
    return ActionResponse(
        status="discarded",
        message=f"Discarded {len(pending_sessions)} session(s), deleted {total_deleted} record(s)",
        deleted_count=total_deleted,
    )

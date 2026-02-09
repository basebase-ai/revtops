"""
Change Session Service for tracking and rolling back agent-made changes.

Part of Phase 3: Change Sessions & Rollback
Provides functionality to:
- Start change sessions for agent tasks
- Capture snapshots before modifications
- Approve or discard changes
- Detect conflicts with concurrent modifications
"""

import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import UUID

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from models.account import Account
from models.activity import Activity
from models.change_session import ChangeSession
from models.contact import Contact
from models.database import get_admin_session, get_session
from models.deal import Deal
from models.record_snapshot import RecordSnapshot

logger = logging.getLogger(__name__)

# Map of table names to model classes
TABLE_MODELS: dict[str, type] = {
    "contacts": Contact,
    "deals": Deal,
    "accounts": Account,
    "activities": Activity,
}


async def start_change_session(
    organization_id: str,
    user_id: str,
    conversation_id: Optional[str] = None,
    description: Optional[str] = None,
) -> ChangeSession:
    """
    Start a new change session for tracking agent modifications.
    
    Call this at the start of an agent task that will modify data.
    
    Args:
        organization_id: Organization UUID
        user_id: User UUID who initiated the changes
        conversation_id: Optional conversation UUID for linking
        description: Optional description of what changes are being made
        
    Returns:
        The created ChangeSession
    """
    async with get_session(organization_id=organization_id) as session:
        change_session = ChangeSession(
            organization_id=UUID(organization_id),
            user_id=UUID(user_id),
            conversation_id=UUID(conversation_id) if conversation_id else None,
            description=description,
            status="pending",
        )
        session.add(change_session)
        await session.commit()
        await session.refresh(change_session)
        
        logger.info(
            f"[ChangeSession] Started session {change_session.id} "
            f"for user {user_id} in org {organization_id}"
        )
        
        return change_session


async def get_or_start_change_session(
    organization_id: str,
    user_id: str,
    scope_conversation_id: str,
    description: Optional[str] = None,
) -> ChangeSession:
    """
    Get an existing pending change session for this scope, or create one.

    Use this when multiple CRM operations (e.g. from one workflow run or chat) should
    be grouped into a single change session for one "Commit all" / "Discard all" review.
    scope_conversation_id is typically the root conversation of a workflow tree, or
    the conversation id for a single chat.

    Args:
        organization_id: Organization UUID
        user_id: User UUID who initiated the changes
        scope_conversation_id: Conversation UUID to group by (root or current)
        description: Optional description (used only when creating)

    Returns:
        The existing or newly created ChangeSession
    """
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(ChangeSession)
            .where(
                ChangeSession.organization_id == UUID(organization_id),
                ChangeSession.conversation_id == UUID(scope_conversation_id),
                ChangeSession.status == "pending",
            )
            .order_by(ChangeSession.created_at)
            .limit(1)
        )
        existing: ChangeSession | None = result.scalar_one_or_none()
        if existing:
            await session.refresh(existing)
            logger.debug(
                "[ChangeSession] Reusing pending session %s for scope %s",
                existing.id,
                scope_conversation_id,
            )
            return existing

    return await start_change_session(
        organization_id=organization_id,
        user_id=user_id,
        conversation_id=scope_conversation_id,
        description=description or f"CRM changes (scope {scope_conversation_id[:8]}...)",
    )


async def get_or_start_orphan_change_session(
    organization_id: str,
    user_id: str,
    description: Optional[str] = None,
) -> ChangeSession:
    """
    Get or create a pending change session for CRM ops with no conversation (e.g. API/orphan).
    Batches all such ops for the same org+user into one session for one Commit/Discard.
    """
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(ChangeSession)
            .where(
                ChangeSession.organization_id == UUID(organization_id),
                ChangeSession.user_id == UUID(user_id),
                ChangeSession.conversation_id.is_(None),
                ChangeSession.status == "pending",
            )
            .order_by(ChangeSession.created_at)
            .limit(1)
        )
        existing: ChangeSession | None = result.scalar_one_or_none()
        if existing:
            await session.refresh(existing)
            return existing
    return await start_change_session(
        organization_id=organization_id,
        user_id=user_id,
        conversation_id=None,
        description=description or "CRM changes (no conversation)",
    )


async def capture_snapshot(
    change_session_id: str,
    table_name: str,
    record_id: str,
    operation: Literal["create", "update", "delete"],
    db_session: Optional[AsyncSession] = None,
    organization_id: str | None = None,
) -> RecordSnapshot:
    """
    Capture a snapshot of a record before modification.
    
    Call this BEFORE modifying a record. For creates, the before_data
    will be null. For updates/deletes, it captures the current state.
    
    Args:
        change_session_id: The change session UUID
        table_name: Table being modified (contacts, deals, accounts, activities)
        record_id: UUID of the record being modified
        operation: Type of operation (create, update, delete)
        db_session: Optional existing database session to use
        
    Returns:
        The created RecordSnapshot
    """
    if table_name not in TABLE_MODELS:
        raise ValueError(f"Unknown table: {table_name}")
    
    model_class = TABLE_MODELS[table_name]
    
    async def _do_capture(session: AsyncSession) -> RecordSnapshot:
        before_data: Optional[dict[str, Any]] = None
        
        # For update/delete, fetch current state
        if operation in ("update", "delete"):
            record = await session.get(model_class, UUID(record_id))
            if record:
                before_data = _record_to_dict(record)
        
        snapshot = RecordSnapshot(
            change_session_id=UUID(change_session_id),
            table_name=table_name,
            record_id=UUID(record_id),
            operation=operation,
            before_data=before_data,
        )
        session.add(snapshot)
        await session.flush()  # Get the ID without committing
        
        logger.debug(
            f"[ChangeSession] Captured snapshot for {table_name}:{record_id} "
            f"op={operation} session={change_session_id}"
        )
        
        return snapshot
    
    if db_session:
        return await _do_capture(db_session)
    else:
        async with get_session(organization_id=organization_id) as session:
            snapshot = await _do_capture(session)
            await session.commit()
            return snapshot


async def update_snapshot_after_data(
    snapshot_id: str,
    after_data: dict[str, Any],
    db_session: Optional[AsyncSession] = None,
    organization_id: str | None = None,
) -> None:
    """
    Update a snapshot with the after_data after modification.
    
    Call this AFTER modifying a record to store the new state.
    
    Args:
        snapshot_id: The snapshot UUID
        after_data: The new state of the record
        db_session: Optional existing database session to use
    """
    async def _do_update(session: AsyncSession) -> None:
        await session.execute(
            update(RecordSnapshot)
            .where(RecordSnapshot.id == UUID(snapshot_id))
            .values(after_data=after_data)
        )
    
    if db_session:
        await _do_update(db_session)
    else:
        async with get_session(organization_id=organization_id) as session:
            await _do_update(session)
            await session.commit()


async def add_proposed_create(
    change_session_id: str,
    table_name: str,
    record_id: str,
    input_payload: dict[str, Any],
    db_session: Optional[AsyncSession] = None,
    organization_id: str | None = None,
) -> RecordSnapshot:
    """
    Store a proposed create (no local row yet). On commit we create locally and push to CRM.
    after_data stores {"_input": input_payload} for HubSpot and local row creation.
    """
    if table_name not in TABLE_MODELS:
        raise ValueError(f"Unknown table: {table_name}")

    snapshot = RecordSnapshot(
        change_session_id=UUID(change_session_id),
        table_name=table_name,
        record_id=UUID(record_id),
        operation="create",
        before_data=None,
        after_data={"_input": input_payload},
    )

    async def _do_add(session: AsyncSession) -> RecordSnapshot:
        session.add(snapshot)
        await session.flush()
        return snapshot

    if db_session:
        return await _do_add(db_session)
    async with get_session(organization_id=organization_id) as session:
        sn = await _do_add(session)
        await session.commit()
        return sn


async def add_proposed_update(
    change_session_id: str,
    table_name: str,
    record_id: str,
    update_fields: dict[str, Any],
    db_session: Optional[AsyncSession] = None,
    organization_id: str | None = None,
) -> RecordSnapshot:
    """
    Store a proposed update. Captures the current row as before_data and stores
    the requested changes in after_data={"_input": update_fields}.
    On commit we push the update to HubSpot and apply locally.
    """
    if table_name not in TABLE_MODELS:
        raise ValueError(f"Unknown table: {table_name}")

    model_class = TABLE_MODELS[table_name]

    async def _do_add(session: AsyncSession) -> RecordSnapshot:
        # Capture current state for before_data
        record = await session.get(model_class, UUID(record_id))
        before_data: Optional[dict[str, Any]] = None
        if record:
            before_data = {}
            for col in record.__table__.columns:
                val = getattr(record, col.key, None)
                if val is not None:
                    before_data[col.key] = str(val) if not isinstance(val, (str, int, float, bool)) else val

        snapshot = RecordSnapshot(
            change_session_id=UUID(change_session_id),
            table_name=table_name,
            record_id=UUID(record_id),
            operation="update",
            before_data=before_data,
            after_data={"_input": update_fields},
        )
        session.add(snapshot)
        await session.flush()
        return snapshot

    if db_session:
        return await _do_add(db_session)
    async with get_session(organization_id=organization_id) as session:
        sn = await _do_add(session)
        await session.commit()
        return sn


async def approve_change_session(
    change_session_id: str,
    resolved_by_user_id: str,
    force: bool = False,
    organization_id: str | None = None,
) -> dict[str, Any]:
    """
    Approve a change session, finalizing the changes.
    
    If conflicts are detected (records modified since snapshot), returns
    conflict info unless force=True.
    
    Args:
        change_session_id: The change session UUID
        resolved_by_user_id: User UUID approving the changes
        force: If True, approve even with conflicts
        organization_id: Organization UUID for RLS context
        
    Returns:
        Result dict with status, conflicts (if any), and snapshot count
    """

    org_id = organization_id
    if not org_id:
        async with get_admin_session() as admin_session:
            change_session = await admin_session.get(ChangeSession, UUID(change_session_id))
            if not change_session:
                return {"status": "error", "error": "Change session not found"}
            org_id = str(change_session.organization_id)

    async with get_session(organization_id=organization_id) as session:

        # Get the change session
        change_session = await session.get(ChangeSession, UUID(change_session_id))
        if not change_session:
            return {"status": "error", "error": "Change session not found"}
        
        if change_session.status != "pending":
            return {
                "status": "error",
                "error": f"Change session already {change_session.status}",
            }
        
        # Get all snapshots
        result = await session.execute(
            select(RecordSnapshot)
            .where(RecordSnapshot.change_session_id == UUID(change_session_id))
        )
        snapshots = list(result.scalars().all())
        
        # Check for conflicts
        if not force:
            conflicts = await _detect_conflicts(session, snapshots)
            if conflicts:
                return {
                    "status": "conflicts",
                    "conflicts": conflicts,
                    "message": "Some records were modified by others. Use force=True to approve anyway.",
                }
        
        # Mark as approved
        change_session.status = "approved"
        change_session.resolved_at = datetime.now(timezone.utc)
        change_session.resolved_by = UUID(resolved_by_user_id)
        
        await session.commit()
        
        logger.info(
            f"[ChangeSession] Approved session {change_session_id} "
            f"with {len(snapshots)} changes by user {resolved_by_user_id}"
        )
        
        return {
            "status": "approved",
            "snapshot_count": len(snapshots),
        }


async def discard_change_session(
    change_session_id: str,
    resolved_by_user_id: str,
    organization_id: str | None = None,
) -> dict[str, Any]:
    """
    Discard a change session, rolling back all changes.
    For proposal-only creates there are no local rows; delete is a no-op.
    """
    async with get_session(organization_id=organization_id) as session:
        # Get the change session
        change_session = await session.get(ChangeSession, UUID(change_session_id))
        if not change_session:
            return {"status": "error", "error": "Change session not found"}
        
        if change_session.status != "pending":
            return {
                "status": "error",
                "error": f"Change session already {change_session.status}",
            }
        
        # Get all snapshots (in reverse order for proper rollback)
        result = await session.execute(
            select(RecordSnapshot)
            .where(RecordSnapshot.change_session_id == UUID(change_session_id))
            .order_by(RecordSnapshot.created_at.desc())
        )
        snapshots = list(result.scalars().all())
        
        rollback_count = 0
        errors: list[str] = []
        
        for snapshot in snapshots:
            try:
                await _rollback_snapshot(session, snapshot)
                rollback_count += 1
            except Exception as e:
                errors.append(f"{snapshot.table_name}:{snapshot.record_id}: {e}")
                logger.error(f"[ChangeSession] Rollback error: {e}")
        
        # Mark as discarded
        change_session.status = "discarded"
        change_session.resolved_at = datetime.now(timezone.utc)
        change_session.resolved_by = UUID(resolved_by_user_id)
        
        await session.commit()
        
        logger.info(
            f"[ChangeSession] Discarded session {change_session_id} "
            f"rolled back {rollback_count}/{len(snapshots)} changes"
        )
        
        result_dict: dict[str, Any] = {
            "status": "discarded",
            "rollback_count": rollback_count,
            "total_snapshots": len(snapshots),
        }
        if errors:
            result_dict["errors"] = errors
        
        return result_dict


async def get_change_session(change_session_id: str) -> Optional[dict[str, Any]]:
    """
    Get a change session with its snapshots.
    
    Args:
        change_session_id: The change session UUID
        
    Returns:
        Dict with session info and snapshots, or None if not found
    """
    async with get_admin_session() as session:
        change_session = await session.get(ChangeSession, UUID(change_session_id))
        if not change_session:
            return None
        
        result = await session.execute(
            select(RecordSnapshot)
            .where(RecordSnapshot.change_session_id == UUID(change_session_id))
            .order_by(RecordSnapshot.created_at)
        )
        snapshots = list(result.scalars().all())
        
        return {
            **change_session.to_dict(),
            "snapshots": [s.to_dict() for s in snapshots],
        }


async def get_pending_sessions_for_conversation(
    conversation_id: str,
) -> list[dict[str, Any]]:
    """
    Get all pending change sessions for a conversation.
    
    Args:
        conversation_id: The conversation UUID
        
    Returns:
        List of change session dicts
    """
    async with get_admin_session() as session:
        result = await session.execute(
            select(ChangeSession)
            .where(
                ChangeSession.conversation_id == UUID(conversation_id),
                ChangeSession.status == "pending",
            )
            .order_by(ChangeSession.created_at)
        )
        sessions = list(result.scalars().all())
        
        return [s.to_dict() for s in sessions]


# =============================================================================
# Private helpers
# =============================================================================

def _record_to_dict(record: Any) -> dict[str, Any]:
    """Convert a model record to a serializable dict."""
    if hasattr(record, "to_dict"):
        return record.to_dict()
    
    # Fallback: extract columns manually
    result: dict[str, Any] = {}
    for column in record.__table__.columns:
        value = getattr(record, column.name)
        if isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, datetime):
            value = value.isoformat()
        result[column.name] = value
    return result


async def _detect_conflicts(
    session: AsyncSession,
    snapshots: list[RecordSnapshot],
) -> list[dict[str, Any]]:
    """
    Detect if any snapshotted records were modified by others.
    
    Compares the record's current updated_at with the snapshot time.
    """
    conflicts: list[dict[str, Any]] = []
    
    for snapshot in snapshots:
        if snapshot.operation == "create":
            # Creates can't have conflicts
            continue
        
        if snapshot.table_name not in TABLE_MODELS:
            continue
        
        model_class = TABLE_MODELS[snapshot.table_name]
        record = await session.get(model_class, snapshot.record_id)
        
        if not record:
            # Record was deleted - that's a conflict for updates
            if snapshot.operation == "update":
                conflicts.append({
                    "table": snapshot.table_name,
                    "record_id": str(snapshot.record_id),
                    "reason": "Record was deleted by another user",
                })
            continue
        
        # Check if updated_at is newer than snapshot
        record_updated_at = getattr(record, "updated_at", None)
        if record_updated_at and snapshot.created_at:
            if record_updated_at > snapshot.created_at:
                conflicts.append({
                    "table": snapshot.table_name,
                    "record_id": str(snapshot.record_id),
                    "reason": "Record was modified by another user",
                    "snapshot_time": snapshot.created_at.isoformat(),
                    "record_updated_at": record_updated_at.isoformat(),
                })
    
    return conflicts


async def _rollback_snapshot(
    session: AsyncSession,
    snapshot: RecordSnapshot,
) -> None:
    """
    Rollback a single snapshot.
    
    - Create: Delete the record
    - Update: Restore before_data
    - Delete: Re-insert (not implemented)
    """
    if snapshot.table_name not in TABLE_MODELS:
        raise ValueError(f"Unknown table: {snapshot.table_name}")
    
    model_class = TABLE_MODELS[snapshot.table_name]
    
    if snapshot.operation == "create":
        # Delete the created record
        await session.execute(
            delete(model_class).where(model_class.id == snapshot.record_id)
        )
        logger.debug(f"[Rollback] Deleted {snapshot.table_name}:{snapshot.record_id}")
        
    elif snapshot.operation == "update":
        # Restore the before_data
        if snapshot.before_data:
            # Filter to only columns that exist on the model
            update_data = {}
            for key, value in snapshot.before_data.items():
                if hasattr(model_class, key) and key != "id":
                    # Convert string UUIDs back to UUID objects for FK columns
                    column = getattr(model_class, key, None)
                    if column is not None and value is not None:
                        col_type = getattr(column, "type", None)
                        if col_type and "UUID" in str(col_type):
                            value = UUID(value) if isinstance(value, str) else value
                    update_data[key] = value
            
            if update_data:
                await session.execute(
                    update(model_class)
                    .where(model_class.id == snapshot.record_id)
                    .values(**update_data)
                )
                logger.debug(
                    f"[Rollback] Restored {snapshot.table_name}:{snapshot.record_id}"
                )
        
    elif snapshot.operation == "delete":
        # Re-insert the deleted record
        # TODO: Implement delete rollback (re-insert from before_data)
        logger.warning(
            f"[Rollback] Delete rollback not implemented for "
            f"{snapshot.table_name}:{snapshot.record_id}"
        )

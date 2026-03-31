"""API routes for the action ledger (connector mutation audit trail)."""
import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from api.auth_middleware import AuthContext, get_current_auth
from models.org_member import OrgMember
from models.action_ledger import ActionLedgerEntry
from models.database import get_admin_session, get_session

router = APIRouter(prefix="/action-ledger", tags=["action-ledger"])
logger = logging.getLogger(__name__)


async def _is_org_admin(*, user_id: UUID, organization_id: UUID) -> bool:
    """Return True when the user is an active org admin for the organization."""
    async with get_admin_session() as session:
        membership = (
            await session.execute(
                select(OrgMember).where(
                    OrgMember.user_id == user_id,
                    OrgMember.organization_id == organization_id,
                    OrgMember.status.in_(("active", "onboarding", "invited")),
                )
            )
        ).scalar_one_or_none()
    return bool(membership and membership.role == "admin")


class ActionLedgerResponse(BaseModel):
    entries: list[dict[str, Any]]
    total: int


@router.get("/{org_id}", response_model=ActionLedgerResponse)
async def list_action_ledger(
    org_id: str,
    auth: AuthContext = Depends(get_current_auth),
    conversation_id: Optional[str] = Query(None),
    connector: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ActionLedgerResponse:
    """List action ledger entries for an organization, newest first."""
    if not auth.organization_id or str(auth.organization_id) != org_id:
        raise HTTPException(status_code=403, detail="Organization mismatch")

    org_uuid = UUID(org_id)
    is_org_admin = auth.is_global_admin or await _is_org_admin(
        user_id=auth.user_id,
        organization_id=org_uuid,
    )

    filters = [ActionLedgerEntry.organization_id == org_uuid]
    if not is_org_admin:
        filters.append(ActionLedgerEntry.user_id == auth.user_id)
    if conversation_id:
        filters.append(ActionLedgerEntry.conversation_id == UUID(conversation_id))
    if connector:
        filters.append(ActionLedgerEntry.connector == connector)
    if entity_type:
        filters.append(ActionLedgerEntry.entity_type == entity_type)
    if entity_id:
        filters.append(ActionLedgerEntry.entity_id == entity_id)

    async with get_session(org_id) as session:
        # Total count
        count_q = select(func.count()).select_from(ActionLedgerEntry).where(*filters)
        total = (await session.execute(count_q)).scalar_one()

        # Paginated entries
        q = (
            select(ActionLedgerEntry)
            .where(*filters)
            .order_by(ActionLedgerEntry.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await session.execute(q)).scalars().all()

    return ActionLedgerResponse(
        entries=[r.to_dict() for r in rows],
        total=total,
    )

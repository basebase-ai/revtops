"""Organization admin checks (shared across API routes)."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from models.database import get_admin_session
from models.org_member import OrgMember


async def user_is_org_admin(*, user_id: UUID, organization_id: UUID) -> bool:
    """Return True when the user is an active organization admin."""
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

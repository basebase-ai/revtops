"""
Waitlist routes for managing Alpha signups.

Endpoints:
- POST /api/waitlist - Submit waitlist form
- GET /api/waitlist/admin - List waitlist entries (admin only)
- POST /api/waitlist/admin/{user_id}/invite - Approve and invite user
- POST /api/waitlist/admin/{user_id}/resend-invite - Resend invite email (invited only)
- POST /api/waitlist/admin/organizations/{organization_id}/grant-credits - Partner tier + credits (global admin)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from api.auth_middleware import AuthContext, require_global_admin
from models.database import get_admin_session
from models.user import User
from services.email import send_invitation_email, send_waitlist_confirmation, send_waitlist_notification

router = APIRouter()
logger = logging.getLogger(__name__)


# =============================================================================
# Request/Response Models
# =============================================================================


class WaitlistSubmitRequest(BaseModel):
    """Request model for waitlist submission."""

    email: EmailStr
    name: str
    title: str
    company_name: str
    num_employees: str  # e.g., "1-10", "11-50", "51-200", "201-500", "500+"
    apps_of_interest: list[str]  # e.g., ["salesforce", "hubspot", "slack"]
    core_needs: list[str]  # e.g., ["query_crm", "insights", "workflows"]


class WaitlistSubmitResponse(BaseModel):
    """Response for waitlist submission."""

    success: bool
    message: str


class WaitlistEntryResponse(BaseModel):
    """Response model for a waitlist entry."""

    id: str
    email: str
    name: Optional[str]
    status: str
    waitlist_data: Optional[dict[str, Any]]
    waitlisted_at: Optional[str]
    invited_at: Optional[str]
    created_at: Optional[str]


class WaitlistListResponse(BaseModel):
    """Response for listing waitlist entries."""

    entries: list[WaitlistEntryResponse]
    total: int


class InviteResponse(BaseModel):
    """Response for inviting a user."""

    success: bool
    message: str
    user_id: str


# =============================================================================
# Public Endpoints
# =============================================================================


@router.post("", response_model=WaitlistSubmitResponse)
async def submit_waitlist(request: WaitlistSubmitRequest) -> WaitlistSubmitResponse:
    """
    Submit a waitlist application.
    
    Creates a new user with status='waitlist' and stores the form data.
    """
    async with get_admin_session() as session:
        # Check if email already exists
        result = await session.execute(
            select(User).where(User.email == request.email)
        )
        existing_user = result.scalar_one_or_none()

        if existing_user:
            if existing_user.status == "waitlist":
                return WaitlistSubmitResponse(
                    success=True,
                    message="You're already on the waitlist! We'll be in touch soon.",
                )
            elif existing_user.status in ("invited", "active"):
                return WaitlistSubmitResponse(
                    success=True,
                    message="You already have access! Sign in to get started.",
                )

        # Create new waitlist user
        waitlist_data: dict[str, Any] = {
            "title": request.title,
            "company_name": request.company_name,
            "num_employees": request.num_employees,
            "apps_of_interest": request.apps_of_interest,
            "core_needs": request.core_needs,
        }

        new_user = User(
            email=request.email,
            name=request.name,
            status="waitlist",
            waitlist_data=waitlist_data,
            waitlisted_at=datetime.utcnow(),
        )
        session.add(new_user)
        await session.commit()

        # Send confirmation email to user
        try:
            await send_waitlist_confirmation(
                to_email=request.email,
                name=request.name,
            )
        except Exception as e:
            print(f"Failed to send waitlist confirmation: {e}")
            # Don't fail the signup if email fails

        # Send notification email to support
        try:
            await send_waitlist_notification(
                applicant_email=request.email,
                applicant_name=request.name,
                waitlist_data=waitlist_data,
            )
        except Exception as e:
            print(f"Failed to send waitlist notification: {e}")
            # Don't fail the signup if notification fails

        return WaitlistSubmitResponse(
            success=True,
            message="You're on the list! We'll email you when it's your turn.",
        )


# =============================================================================
# Admin Endpoints (JWT + role-based auth)
# =============================================================================


async def _fetch_waitlist_entries(status: Optional[str]) -> WaitlistListResponse:
    """Fetch waitlist entries with optional status filter. Uses admin session for global admin list.
    Excludes status='active' so that users who have signed up and completed onboarding no longer
    appear in the waitlist (they've graduated). Keeps 'waitlist' and 'invited' for resend/outreach.
    """
    async with get_admin_session() as session:
        query = (
            select(User)
            .where(User.waitlisted_at.isnot(None))
            .where(User.status != "active")
        )

        if status and status != "all":
            query = query.where(User.status == status)

        query = query.order_by(User.waitlisted_at.desc())

        result = await session.execute(query)
        users = result.scalars().all()

        entries = [
            WaitlistEntryResponse(
                id=str(u.id),
                email=u.email,
                name=u.name,
                status=u.status,
                waitlist_data=u.waitlist_data,
                waitlisted_at=f"{u.waitlisted_at.isoformat()}Z" if u.waitlisted_at else None,
                invited_at=f"{u.invited_at.isoformat()}Z" if u.invited_at else None,
                created_at=f"{u.created_at.isoformat()}Z" if u.created_at else None,
            )
            for u in users
        ]

        return WaitlistListResponse(entries=entries, total=len(entries))


@router.get("/admin", response_model=WaitlistListResponse)
async def list_waitlist(
    status: Optional[str] = None,
    auth: AuthContext = Depends(require_global_admin),
) -> WaitlistListResponse:
    """
    List all waitlist entries.
    
    Requires global_admin role through verified JWT auth.
    Filter by status: 'waitlist', 'invited', or 'all'.
    """
    logger.info("Admin waitlist list requested by user_id=%s status=%s", auth.user_id, status or "all")
    return await _fetch_waitlist_entries(status)


@router.post("/admin/{user_id}/invite", response_model=InviteResponse)
async def invite_user(
    user_id: str,
    auth: AuthContext = Depends(require_global_admin),
) -> InviteResponse:
    """
    Invite a user from the waitlist.
    
    Sets status to 'invited' and sends invitation email.
    """
    logger.info("Admin waitlist invite requested by user_id=%s target_user_id=%s", auth.user_id, user_id)

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    async with get_admin_session() as session:
        user = await session.get(User, user_uuid)
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if user.status == "active":
            return InviteResponse(
                success=False,
                message="User is already active",
                user_id=str(user.id),
            )
        
        if user.status == "invited":
            return InviteResponse(
                success=False,
                message="User has already been invited",
                user_id=str(user.id),
            )

        # Update status
        user.status = "invited"
        user.invited_at = datetime.utcnow()
        await session.commit()

        # Send invitation email
        try:
            await send_invitation_email(
                to_email=user.email,
                name=user.name or "there",
            )
        except Exception as e:
            print(f"Failed to send invitation email: {e}")
            # Don't fail the request if email fails - user is still invited

        return InviteResponse(
            success=True,
            message=f"Invitation sent to {user.email}",
            user_id=str(user.id),
        )


@router.post("/admin/{user_id}/resend-invite", response_model=InviteResponse)
async def resend_waitlist_invite(
    user_id: str,
    auth: AuthContext = Depends(require_global_admin),
) -> InviteResponse:
    """
    Resend invitation email to a user with status='invited'.
    For follow-up when invitee hasn't responded.
    """
    logger.info("Admin waitlist resend-invite requested by user_id=%s target_user_id=%s", auth.user_id, user_id)

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    async with get_admin_session() as session:
        user = await session.get(User, user_uuid)

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if user.status != "invited":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot resend invite: user status is '{user.status}', not 'invited'",
            )

        try:
            sent: bool = await send_invitation_email(
                to_email=user.email,
                name=user.name or "there",
            )
            if sent:
                user.invited_at = datetime.utcnow()
                await session.commit()
                return InviteResponse(
                    success=True,
                    message=f"Invitation re-sent to {user.email}",
                    user_id=str(user.id),
                )
            return InviteResponse(
                success=False,
                message=f"Failed to send email (check RESEND_API_KEY)",
                user_id=str(user.id),
            )
        except Exception as e:
            print(f"Failed to send resend invitation email: {e}")
            return InviteResponse(
                success=False,
                message=f"Email delivery failed: {e!s}",
                user_id=str(user.id),
            )


# =============================================================================
@router.get("/admin/list", response_model=WaitlistListResponse)
async def list_waitlist_role_auth(
    status: Optional[str] = None,
    auth: AuthContext = Depends(require_global_admin),
) -> WaitlistListResponse:
    """
    List all waitlist entries.
    
    Requires user to have global_admin role.
    Filter by status: 'waitlist', 'invited', or 'all'.
    """
    logger.info("Admin waitlist list(alias) requested by user_id=%s status=%s", auth.user_id, status or "all")
    return await _fetch_waitlist_entries(status)


@router.post("/admin/{target_user_id}/invite", response_model=InviteResponse)
async def invite_user_role_auth(
    target_user_id: str,
    auth: AuthContext = Depends(require_global_admin),
) -> InviteResponse:
    """
    Invite a user from the waitlist.
    
    Requires user to have global_admin role.
    Sets status to 'invited' and sends invitation email.
    """
    logger.info("Admin waitlist invite(alias) requested by user_id=%s target_user_id=%s", auth.user_id, target_user_id)

    try:
        target_uuid = UUID(target_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid target user ID")

    async with get_admin_session() as session:
        user = await session.get(User, target_uuid)
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if user.status == "active":
            return InviteResponse(
                success=False,
                message="User is already active",
                user_id=str(user.id),
            )
        
        if user.status == "invited":
            return InviteResponse(
                success=False,
                message="User has already been invited",
                user_id=str(user.id),
            )

        # Update status
        user.status = "invited"
        user.invited_at = datetime.utcnow()
        await session.commit()

        # Send invitation email
        try:
            await send_invitation_email(
                to_email=user.email,
                name=user.name or "there",
            )
        except Exception as e:
            print(f"Failed to send invitation email: {e}")
            # Don't fail the request if email fails - user is still invited

        return InviteResponse(
            success=True,
            message=f"Invitation sent to {user.email}",
            user_id=str(user.id),
        )


# =============================================================================
# Admin Users List Endpoint
# =============================================================================


class AdminUserResponse(BaseModel):
    """Response model for a user in the admin users list."""

    id: str
    email: str
    first_name: Optional[str]
    last_name: Optional[str]
    status: str
    last_login: Optional[str]
    created_at: Optional[str]
    organization_id: Optional[str]
    organization_name: Optional[str]
    organizations: list[str] = Field(default_factory=list)
    is_guest: bool = False


class AdminUsersListResponse(BaseModel):
    """Response for listing all users."""

    users: list[AdminUserResponse]
    total: int


@router.get("/admin/users", response_model=AdminUsersListResponse)
async def list_admin_users(
    auth: AuthContext = Depends(require_global_admin),
) -> AdminUsersListResponse:
    """
    List all users who are not on the waitlist (active or invited).
    
    Requires user to have global_admin role.
    Returns users with their organization info and last login time.
    """
    logger.info("Admin users list requested by user_id=%s", auth.user_id)

    from models.org_member import OrgMember
    from models.organization import Organization
    from sqlalchemy.orm import selectinload

    async with get_admin_session() as session:
        # Get all users who are not on the waitlist (active or invited)
        query = (
            select(User)
            .options(selectinload(User.guest_organization))
            .where(User.status.in_(["active", "invited"]))
            .order_by(User.created_at.desc())
        )
        
        result = await session.execute(query)
        users = result.scalars().all()

        user_ids = [u.id for u in users]
        memberships_by_user: dict[UUID, list[tuple[UUID, str]]] = {}

        if user_ids:
            memberships_result = await session.execute(
                select(OrgMember.user_id, Organization.id, Organization.name)
                .join(Organization, OrgMember.organization_id == Organization.id)
                .where(
                    OrgMember.user_id.in_(user_ids),
                    OrgMember.status.in_(["active", "onboarding", "invited"]),
                )
                .order_by(Organization.name.asc())
            )
            for row_uid, row_oid, organization_name in memberships_result.all():
                memberships_by_user.setdefault(row_uid, []).append(
                    (row_oid, organization_name)
                )

        def split_name(full_name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
            """Split a full name into first and last name."""
            if not full_name:
                return (None, None)
            parts = full_name.strip().split(" ", 1)
            first_name = parts[0] if parts else None
            last_name = parts[1] if len(parts) > 1 else None
            return (first_name, last_name)

        user_responses: list[AdminUserResponse] = []
        for u in users:
            first_name, last_name = split_name(u.name)
            org_name: Optional[str] = None
            org_id_out: Optional[str] = None
            if u.guest_organization:
                org_name = u.guest_organization.name
            membership_rows: list[tuple[UUID, str]] = memberships_by_user.get(u.id, [])
            if u.is_guest and u.guest_organization_id:
                org_id_out = str(u.guest_organization_id)
            elif membership_rows:
                org_id_out = str(membership_rows[0][0])
                if not org_name:
                    org_name = membership_rows[0][1]

            organizations: list[str] = [row[1] for row in membership_rows]
            user_responses.append(
                AdminUserResponse(
                    id=str(u.id),
                    email=u.email,
                    first_name=first_name,
                    last_name=last_name,
                    status=u.status,
                    last_login=f"{u.last_login.isoformat()}Z" if u.last_login else None,
                    created_at=f"{u.created_at.isoformat()}Z" if u.created_at else None,
                    organization_id=org_id_out,
                    organization_name=org_name,
                    organizations=organizations,
                    is_guest=bool(u.is_guest),
                )
            )

        return AdminUsersListResponse(users=user_responses, total=len(user_responses))


@router.delete("/admin/users/{user_id}")
async def delete_admin_user(
    user_id: str,
    auth: AuthContext = Depends(require_global_admin),
) -> dict[str, str]:
    """
    Permanently delete a user and all their data.

    Requires global_admin role. Use for resetting test accounts or GDPR-style removal.
    """
    from services.user_merge import delete_user

    logger.info("Admin delete user requested by user_id=%s target_user_id=%s", auth.user_id, user_id)

    result = await delete_user(user_id)
    if not result.success:
        if "not found" in (result.error or "").lower():
            raise HTTPException(status_code=404, detail=result.error)
        raise HTTPException(status_code=400, detail=result.error or "Failed to delete user")

    return {"status": "deleted", "email": result.email}


# =============================================================================
# Admin Organizations List Endpoint
# =============================================================================


class AdminOrganizationResponse(BaseModel):
    """Response model for an organization in the admin list."""

    id: str
    name: str
    email_domain: Optional[str]
    user_count: int
    credits_balance: int
    credits_included: int
    created_at: Optional[str]
    last_sync_at: Optional[str]


class AdminOrganizationsListResponse(BaseModel):
    """Response for listing all organizations."""

    organizations: list[AdminOrganizationResponse]
    total: int


class GrantFreeCreditsRequest(BaseModel):
    """Same billing fields as scripts/grant_free_credits.py (partner tier, period, credits)."""

    credits: int = Field(default=2000, ge=1, le=10_000_000)
    months: int = Field(default=12, ge=1, le=120)


class GrantFreeCreditsResponse(BaseModel):
    """Confirmation after granting free credits."""

    success: bool
    organization_id: str
    organization_name: str
    credits_balance: int
    credits_included: int
    subscription_tier: Optional[str]
    subscription_status: Optional[str]
    current_period_end: Optional[str]


class AdminCreateOrganizationRequest(BaseModel):
    """Request model for admin creating an organization."""

    name: str
    email_domain: str
    logo_url: Optional[str] = None


@router.post("/admin/organizations", response_model=AdminOrganizationResponse)
async def create_admin_organization(
    request: AdminCreateOrganizationRequest,
    auth: AuthContext = Depends(require_global_admin),
) -> AdminOrganizationResponse:
    """
    Create a new organization and add the current user as admin member.

    Requires global_admin role. Duplicate email_domain returns 409.
    """
    from models.organization import Organization
    from models.org_member import OrgMember

    logger.info("Admin create organization requested by user_id=%s", auth.user_id)

    name_trimmed: str = (request.name or "").strip()
    domain_trimmed: str = (request.email_domain or "").strip().lower()
    if not name_trimmed:
        raise HTTPException(status_code=400, detail="Organization name is required")
    if not domain_trimmed or "@" in domain_trimmed:
        raise HTTPException(status_code=400, detail="Valid email domain is required (e.g. acme.com)")

    async with get_admin_session() as session:
        existing = await session.execute(
            select(Organization).where(Organization.email_domain == domain_trimmed)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=409,
                detail="An organization with this email domain already exists",
            )

        new_org = Organization(
            id=uuid.uuid4(),
            name=name_trimmed,
            email_domain=domain_trimmed,
            logo_url=(request.logo_url or "").strip() or None,
        )
        session.add(new_org)
        await session.flush()

        # Auto-enable web_search, artifacts, and apps for new organizations
        from config import get_provider_sharing_defaults
        from models.integration import Integration

        sharing_defaults = get_provider_sharing_defaults("web_search")
        web_search_integration = Integration(
            organization_id=new_org.id,
            provider="web_search",
            user_id=auth.user_id,
            scope="organization",
            nango_connection_id="builtin",
            connected_by_user_id=auth.user_id,
            is_active=True,
            share_synced_data=sharing_defaults.share_synced_data,
            share_query_access=sharing_defaults.share_query_access,
            share_write_access=sharing_defaults.share_write_access,
            pending_sharing_config=False,
        )
        session.add(web_search_integration)
        artifacts_defaults = get_provider_sharing_defaults("artifacts")
        artifacts_integration = Integration(
            organization_id=new_org.id,
            provider="artifacts",
            user_id=auth.user_id,
            scope="organization",
            nango_connection_id="builtin",
            connected_by_user_id=auth.user_id,
            is_active=True,
            share_synced_data=artifacts_defaults.share_synced_data,
            share_query_access=artifacts_defaults.share_query_access,
            share_write_access=artifacts_defaults.share_write_access,
            pending_sharing_config=False,
        )
        session.add(artifacts_integration)
        apps_defaults = get_provider_sharing_defaults("apps")
        apps_integration = Integration(
            organization_id=new_org.id,
            provider="apps",
            user_id=auth.user_id,
            scope="organization",
            nango_connection_id="builtin",
            connected_by_user_id=auth.user_id,
            is_active=True,
            share_synced_data=apps_defaults.share_synced_data,
            share_query_access=apps_defaults.share_query_access,
            share_write_access=apps_defaults.share_write_access,
            pending_sharing_config=False,
        )
        session.add(apps_integration)

        membership = OrgMember(
            user_id=auth.user_id,
            organization_id=new_org.id,
            role="admin",
            status="active",
            joined_at=datetime.utcnow(),
        )
        session.add(membership)
        await session.commit()
        await session.refresh(new_org)

        return AdminOrganizationResponse(
            id=str(new_org.id),
            name=new_org.name,
            email_domain=new_org.email_domain,
            user_count=1,
            credits_balance=new_org.credits_balance,
            credits_included=new_org.credits_included,
            created_at=f"{new_org.created_at.isoformat()}Z" if new_org.created_at else None,
            last_sync_at=f"{new_org.last_sync_at.isoformat()}Z" if new_org.last_sync_at else None,
        )


@router.get("/admin/organizations", response_model=AdminOrganizationsListResponse)
async def list_admin_organizations(
    auth: AuthContext = Depends(require_global_admin),
) -> AdminOrganizationsListResponse:
    """
    List all organizations.
    
    Requires user to have global_admin role.
    Returns organizations with user counts.
    """
    logger.info("Admin organizations list requested by user_id=%s", auth.user_id)

    from models.organization import Organization
    from models.org_member import OrgMember

    async with get_admin_session() as session:
        # Members are org_members → users (Organization.users was removed with users.organization_id)
        member_counts = (
            select(
                OrgMember.organization_id.label("org_id"),
                func.count(User.id).label("user_count"),
            )
            .select_from(OrgMember)
            .join(User, User.id == OrgMember.user_id)
            .where(User.status.in_(("active", "invited")))
            .group_by(OrgMember.organization_id)
        ).subquery()

        query = (
            select(Organization, func.coalesce(member_counts.c.user_count, 0))
            .outerjoin(member_counts, member_counts.c.org_id == Organization.id)
            .order_by(Organization.created_at.desc())
        )

        result = await session.execute(query)
        rows: list[tuple[Organization, int]] = list(result.all())

        org_responses: list[AdminOrganizationResponse] = []
        for org, active_user_count in rows:
            org_responses.append(
                AdminOrganizationResponse(
                    id=str(org.id),
                    name=org.name,
                    email_domain=org.email_domain,
                    user_count=int(active_user_count),
                    credits_balance=org.credits_balance,
                    credits_included=org.credits_included,
                    created_at=f"{org.created_at.isoformat()}Z" if org.created_at else None,
                    last_sync_at=f"{org.last_sync_at.isoformat()}Z" if org.last_sync_at else None,
                )
            )

        return AdminOrganizationsListResponse(organizations=org_responses, total=len(org_responses))


@router.post(
    "/admin/organizations/{organization_id}/grant-credits",
    response_model=GrantFreeCreditsResponse,
)
async def grant_organization_free_credits(
    organization_id: UUID,
    body: GrantFreeCreditsRequest,
    auth: AuthContext = Depends(require_global_admin),
) -> GrantFreeCreditsResponse:
    """
    Grant partner-tier access with free credits and a fixed billing period.

    Mirrors backend/scripts/grant_free_credits.py. Requires global_admin.
    """
    from models.organization import Organization

    logger.info(
        "Admin grant free credits org_id=%s credits=%s months=%s by user_id=%s",
        organization_id,
        body.credits,
        body.months,
        auth.user_id,
    )

    now = datetime.now(timezone.utc)
    period_end = now + timedelta(days=30 * body.months)

    async with get_admin_session() as session:
        result = await session.execute(
            select(Organization).where(Organization.id == organization_id)
        )
        org: Organization | None = result.scalar_one_or_none()
        if org is None:
            raise HTTPException(status_code=404, detail="Organization not found")

        org.subscription_tier = "partner"
        org.subscription_status = "active"
        org.credits_balance = body.credits
        org.credits_included = body.credits
        org.current_period_start = now
        org.current_period_end = period_end
        org.stripe_customer_id = None
        org.stripe_subscription_id = None

        await session.commit()
        await session.refresh(org)

    period_end_str: Optional[str] = (
        org.current_period_end.isoformat() if org.current_period_end else None
    )

    return GrantFreeCreditsResponse(
        success=True,
        organization_id=str(org.id),
        organization_name=org.name,
        credits_balance=org.credits_balance,
        credits_included=org.credits_included,
        subscription_tier=org.subscription_tier,
        subscription_status=org.subscription_status,
        current_period_end=period_end_str,
    )

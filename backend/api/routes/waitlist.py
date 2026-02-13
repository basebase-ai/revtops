"""
Waitlist routes for managing early access signups.

Endpoints:
- POST /api/waitlist - Submit waitlist form
- GET /api/admin/waitlist - List waitlist entries (admin only)
- POST /api/admin/waitlist/{user_id}/invite - Approve and invite user
"""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from api.auth_middleware import AuthContext, require_global_admin
from models.database import get_session
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
    core_needs: list[str]  # e.g., ["query_crm", "insights", "automations"]


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
    async with get_session() as session:
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
# Admin Endpoints
# =============================================================================


@router.get("/admin", response_model=WaitlistListResponse)
async def list_waitlist(
    status: Optional[str] = None,
    auth: AuthContext = Depends(require_global_admin),
) -> WaitlistListResponse:
    """
    List all waitlist entries.
    
    Requires a valid JWT for a global admin user.
    Filter by status: 'waitlist', 'invited', or 'all'.
    """
    logger.info("Admin waitlist list requested by user_id=%s status=%s", auth.user_id, status)

    async with get_session() as session:
        query = select(User).where(User.waitlisted_at.isnot(None))
        
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

    async with get_session() as session:
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
    logger.info("Admin waitlist list(v2) requested by user_id=%s status=%s", auth.user_id, status)

    async with get_session() as session:
        query = select(User).where(User.waitlisted_at.isnot(None))
        
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
    logger.info("Admin waitlist invite(v2) requested by user_id=%s target_user_id=%s", auth.user_id, target_user_id)

    try:
        target_uuid = UUID(target_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid target user ID")

    async with get_session() as session:
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

    from sqlalchemy.orm import selectinload

    async with get_session() as session:
        # Get all users who are not on the waitlist (active or invited)
        query = (
            select(User)
            .options(selectinload(User.organization))
            .where(User.status.in_(["active", "invited"]))
            .order_by(User.created_at.desc())
        )
        
        result = await session.execute(query)
        users = result.scalars().all()

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
            if u.organization:
                org_name = u.organization.name
            
            user_responses.append(
                AdminUserResponse(
                    id=str(u.id),
                    email=u.email,
                    first_name=first_name,
                    last_name=last_name,
                    status=u.status,
                    last_login=f"{u.last_login.isoformat()}Z" if u.last_login else None,
                    created_at=f"{u.created_at.isoformat()}Z" if u.created_at else None,
                    organization_id=str(u.organization_id) if u.organization_id else None,
                    organization_name=org_name,
                )
            )

        return AdminUsersListResponse(users=user_responses, total=len(user_responses))


# =============================================================================
# Admin Organizations List Endpoint
# =============================================================================


class AdminOrganizationResponse(BaseModel):
    """Response model for an organization in the admin list."""

    id: str
    name: str
    email_domain: Optional[str]
    user_count: int
    created_at: Optional[str]
    last_sync_at: Optional[str]


class AdminOrganizationsListResponse(BaseModel):
    """Response for listing all organizations."""

    organizations: list[AdminOrganizationResponse]
    total: int


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
    from sqlalchemy import func
    from sqlalchemy.orm import selectinload

    async with get_session() as session:
        # Get all organizations with their users loaded
        query = (
            select(Organization)
            .options(selectinload(Organization.users))
            .order_by(Organization.created_at.desc())
        )
        
        result = await session.execute(query)
        organizations = result.scalars().all()

        org_responses: list[AdminOrganizationResponse] = []
        for org in organizations:
            # Count only active/invited users (not waitlisted)
            active_user_count = len([u for u in org.users if u.status in ("active", "invited")])
            
            org_responses.append(
                AdminOrganizationResponse(
                    id=str(org.id),
                    name=org.name,
                    email_domain=org.email_domain,
                    user_count=active_user_count,
                    created_at=f"{org.created_at.isoformat()}Z" if org.created_at else None,
                    last_sync_at=f"{org.last_sync_at.isoformat()}Z" if org.last_sync_at else None,
                )
            )

        return AdminOrganizationsListResponse(organizations=org_responses, total=len(org_responses))

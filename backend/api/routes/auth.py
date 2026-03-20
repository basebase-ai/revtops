"""
Authentication routes using Nango for OAuth.

Nango handles all OAuth complexity:
- OAuth flows and consent screens
- Token storage and encryption
- Automatic token refresh

Endpoints:
- GET /api/auth/connect/{provider} - Get Nango connect URL
- GET /api/auth/oauth/callback - Redirect to Nango OAuth callback (preserves query params)
- POST /api/auth/callback - Handle Nango OAuth callback
- GET /api/auth/integrations - List connected integrations
- DELETE /api/auth/integrations/{provider} - Disconnect integration
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import httpx
import json
import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from api.auth_middleware import AuthContext, get_current_auth
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError

from config import (
    BUILTIN_CONNECTORS,
    settings,
    get_nango_integration_id,
    get_provider_sharing_defaults,
    PROVIDER_SHARING_DEFAULTS,
)
from models.database import get_admin_session, get_session
from models.integration import Integration
from models.user import User
from models.organization import Organization
from services.favicon import update_org_logo_from_website
from services.nango import extract_connection_metadata, get_nango_client
from services.slack_identity import upsert_slack_user_mappings_from_metadata

router = APIRouter()
logger = logging.getLogger(__name__)

MEMBER_ACTIVE_STATUSES: tuple[str, ...] = ("active", "onboarding")


def _slugify_domain(domain: str) -> str:
    """Convert email_domain to URL-safe handle (e.g. orangeco.com → orangeco)."""
    import re
    s = (domain or "").strip().lower()
    # Strip common TLDs
    s = re.sub(r"\.(com|co|io|org|net|ai|app|dev|xyz|tech)(\.[a-z]{2})?$", "", s)
    # Keep only alphanumeric and hyphen
    s = re.sub(r"[^a-z0-9-]", "", s)
    return s[:64] if s else "org"


async def _unique_handle(session: Any, base: str) -> str:
    """Return base or base-N if base is taken (N=2,3,...)."""
    handle = base
    n = 2
    while True:
        existing = await session.execute(
            select(Organization).where(
                Organization.handle.isnot(None),
                func.lower(Organization.handle) == handle.lower(),
            )
        )
        if existing.scalar_one_or_none() is None:
            return handle
        handle = f"{base}-{n}"
        n += 1


_scope_by_provider_cache: dict[str, str] | None = None


def _get_scope_by_provider() -> dict[str, str]:
    """Return a cached mapping of provider slug -> scope value."""
    global _scope_by_provider_cache
    if _scope_by_provider_cache is None:
        from connectors.registry import ConnectorScope, discover_connectors

        registry = discover_connectors()
        _scope_by_provider_cache = {
            slug: (
                cls.meta.scope.value  # type: ignore[attr-defined]
                if hasattr(cls, "meta") and hasattr(cls.meta, "scope")
                else ConnectorScope.USER.value
            )
            for slug, cls in registry.items()
        }
    return _scope_by_provider_cache


_DRIVE_LOGIN_SYNC_MIN_INTERVAL = timedelta(minutes=5)


def _should_trigger_drive_sync_on_login(last_sync_at: Optional[datetime]) -> bool:
    """Throttle login-triggered Drive syncs to at most once every 5 minutes."""
    if last_sync_at is None:
        return True
    return (datetime.utcnow() - last_sync_at) >= _DRIVE_LOGIN_SYNC_MIN_INTERVAL


def _enqueue_google_drive_login_sync(organization_id: UUID, user_id: UUID, integration: Integration) -> None:
    """Queue a Google Drive sync for the logging-in user when throttling allows."""
    if not _should_trigger_drive_sync_on_login(integration.last_sync_at):
        logger.info(
            "Skipping login-triggered Google Drive sync org=%s user=%s last_sync_at=%s",
            organization_id,
            user_id,
            integration.last_sync_at.isoformat() if integration.last_sync_at else None,
        )
        return

    try:
        from workers.tasks.sync import sync_integration

        task = sync_integration.delay(str(organization_id), "google_drive", str(user_id))
        logger.info(
            "Queued login-triggered Google Drive sync org=%s user=%s task_id=%s",
            organization_id,
            user_id,
            task.id,
        )
    except Exception as exc:
        logger.warning(
            "Failed to queue login-triggered Google Drive sync org=%s user=%s error=%s",
            organization_id,
            user_id,
            exc,
        )


def _is_global_admin(user: Optional[User]) -> bool:
    """Return True when the user has the global admin role."""
    if not user:
        return False
    return user.role == "global_admin" or "global_admin" in (user.roles or [])


async def _get_org_membership(session: Any, user_id: UUID, org_id: UUID) -> Optional[Any]:
    """Load a user's membership in an organization."""
    from models.org_member import OrgMember

    result = await session.execute(
        select(OrgMember).where(
            OrgMember.user_id == user_id,
            OrgMember.organization_id == org_id,
            OrgMember.status.in_(MEMBER_ACTIVE_STATUSES),
        )
    )
    return result.scalar_one_or_none()


async def _get_user_role_for_active_org(session: Any, user: Optional[User]) -> Optional[str]:
    """Resolve the user's org-scoped role for their active organization."""
    if not user or not user.organization_id:
        return None

    membership = await _get_org_membership(session, user.id, user.organization_id)
    if membership:
        return membership.role
    return None


async def _can_administer_org(session: Any, user: Optional[User], org_id: UUID) -> bool:
    """Org-admin check: org-scoped admin for this org OR global_admin."""
    if _is_global_admin(user):
        return True
    if not user:
        return False
    membership = await _get_org_membership(session, user.id, org_id)
    return bool(membership and membership.role == "admin")


async def _ensure_org_has_admin(session: Any, org_id: UUID) -> None:
    """Ensure each org has at least one non-guest active admin membership."""
    from models.org_member import OrgMember

    admin_result = await session.execute(
        select(OrgMember.id)
        .join(User, User.id == OrgMember.user_id)
        .where(
            OrgMember.organization_id == org_id,
            OrgMember.status.in_(MEMBER_ACTIVE_STATUSES),
            OrgMember.role == "admin",
            User.is_guest.is_(False),
        )
        .limit(1)
    )
    if admin_result.scalar_one_or_none():
        return

    first_member_result = await session.execute(
        select(OrgMember)
        .join(User, User.id == OrgMember.user_id)
        .where(
            OrgMember.organization_id == org_id,
            OrgMember.status.in_(MEMBER_ACTIVE_STATUSES),
            User.is_guest.is_(False),
        )
        .order_by(OrgMember.joined_at.asc().nulls_last(), OrgMember.created_at.asc().nulls_last())
        .limit(1)
    )
    first_member = first_member_result.scalar_one_or_none()
    if not first_member:
        return

    first_member.role = "admin"

    logger.info(
        "Promoted first active org member to admin org=%s user=%s",
        org_id,
        first_member.user_id,
    )


_NANGO_SENSITIVE_KEYS = {"credentials", "access_token", "refresh_token", "api_key", "apiKey", "token"}
_NANGO_HIGHLIGHT_KEYS = {"end_user", "errors", "id", "last_fetched_at", "metadata"}


def _truncate_nango_value(value: str, limit: int = 500) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}... (truncated {len(value) - limit} chars)"


def _format_nango_value(key: str, value: Any) -> str:
    if key in _NANGO_SENSITIVE_KEYS:
        return "<redacted>"
    if isinstance(value, str):
        return _truncate_nango_value(value)
    try:
        serialized = json.dumps(value, default=str, ensure_ascii=False)
    except TypeError:
        serialized = repr(value)
    return _truncate_nango_value(serialized)


def _log_slack_nango_connection(connection: dict[str, Any], connection_id: str) -> None:
    keys = sorted(connection.keys())
    logger.info(
        "[Confirm] Nango Slack connection payload keys for connection_id=%s: %s",
        connection_id,
        keys,
    )
    for key in keys:
        logger.info(
            "[Confirm] Nango Slack connection field connection_id=%s key=%s value=%s",
            connection_id,
            key,
            _format_nango_value(key, connection.get(key)),
        )
    missing_highlights = sorted(_NANGO_HIGHLIGHT_KEYS - set(keys))
    if missing_highlights:
        logger.info(
            "[Confirm] Nango Slack connection missing expected keys connection_id=%s missing=%s",
            connection_id,
            missing_highlights,
        )
    else:
        logger.info(
            "[Confirm] Nango Slack connection contains all highlight keys connection_id=%s keys=%s",
            connection_id,
            sorted(_NANGO_HIGHLIGHT_KEYS),
        )

# =============================================================================
# Response Models
# =============================================================================


class PasswordResetRequest(BaseModel):
    """Request model for initiating password reset emails."""

    email: str


class PasswordResetResponse(BaseModel):
    """Response model for password reset requests."""

    success: bool
    message: str


@router.post("/password-reset/request", response_model=PasswordResetResponse)
async def request_password_reset(request: PasswordResetRequest) -> PasswordResetResponse:
    """Request a password reset email through Supabase Auth with server-side logging."""
    email: str = request.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")

    if not settings.SUPABASE_URL or not settings.SUPABASE_ANON_KEY:
        logger.error("Password reset unavailable due to missing Supabase config")
        raise HTTPException(status_code=500, detail="Password reset is temporarily unavailable")

    redirect_to = f"{settings.FRONTEND_URL.rstrip('/')}/auth"
    payload = {"email": email, "redirect_to": redirect_to}
    headers = {
        "apikey": settings.SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/recover",
                headers=headers,
                json=payload,
            )

        if not (200 <= response.status_code < 300):
            logger.error(
                "Supabase password reset request failed email=%s status=%s body=%s",
                email,
                response.status_code,
                response.text,
            )
            raise HTTPException(status_code=502, detail="Failed to send password reset email")

        logger.info("Password reset email requested successfully for email=%s", email)
        return PasswordResetResponse(
            success=True,
            message="If your account exists, check your email for a password reset link.",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected password reset failure for email=%s", email)
        raise HTTPException(status_code=500, detail="Failed to request password reset") from exc



class UserResponse(BaseModel):
    """Response model for user info."""

    id: str
    email: str
    name: Optional[str]
    role: Optional[str]
    avatar_url: Optional[str]
    phone_number: Optional[str]
    job_title: Optional[str]
    organization_id: Optional[str]
    sms_consent: bool = False
    whatsapp_consent: bool = False
    phone_number_verified: bool = False


class TeamConnection(BaseModel):
    """A team member who has connected a user-scoped integration."""
    
    user_id: str
    user_name: str


class IntegrationResponse(BaseModel):
    """Response model for integration status."""

    id: str
    provider: str
    is_active: bool
    last_sync_at: Optional[str]
    last_error: Optional[str]
    connected_at: Optional[str]
    scope: str = "user"
    # Owner info
    user_id: Optional[str] = None
    connected_by: Optional[str] = None  # Display name of owner
    # Sharing settings
    share_synced_data: bool = False
    share_query_access: bool = False
    share_write_access: bool = False
    pending_sharing_config: bool = False
    # Whether current user owns this integration
    is_owner: bool = False
    # Team connections (other users who have connected this provider)
    current_user_connected: bool = False
    team_connections: list[TeamConnection] = []
    team_total: int = 0
    # Sync statistics
    sync_stats: Optional[dict[str, int]] = None
    # Optional display name override (e.g. user-provided name for MCP connectors)
    display_name: Optional[str] = None


class IntegrationsListResponse(BaseModel):
    """Response model for list of integrations."""

    integrations: list[IntegrationResponse]


class ConnectUrlResponse(BaseModel):
    """Response model for Nango connect URL."""

    connect_url: str
    provider: str


class ConnectSessionResponse(BaseModel):
    """Response model for Nango connect session token."""

    session_token: str
    provider: str
    expires_at: Optional[str]
    connection_id: str


class AvailableIntegrationsResponse(BaseModel):
    """Response model for available integrations."""

    integrations: list[dict[str, str]]


# =============================================================================
# User Auth Endpoints
# =============================================================================


@router.get("/me", response_model=UserResponse)
async def get_current_user(user_id: Optional[str] = None) -> UserResponse:
    """Get current authenticated user."""
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    from models.org_member import OrgMember

    async with get_admin_session() as session:
        user = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Look up job title from organization membership
        job_title: Optional[str] = None
        if user.organization_id:
            membership_result = await session.execute(
                select(OrgMember.title).where(
                    OrgMember.user_id == user_uuid,
                    OrgMember.organization_id == user.organization_id,
                )
            )
            job_title = membership_result.scalar_one_or_none()

        return UserResponse(
            id=str(user.id),
            email=user.email,
            name=user.name,
            role=await _get_user_role_for_active_org(session, user),
            avatar_url=user.avatar_url,
            phone_number=user.phone_number,
            job_title=job_title,
            organization_id=str(user.organization_id) if user.organization_id else None,
            sms_consent=user.sms_consent,
            whatsapp_consent=user.whatsapp_consent,
            phone_number_verified=user.phone_number_verified_at is not None,
        )


class UpdateProfileRequest(BaseModel):
    """Request model for updating user profile."""

    name: Optional[str] = None
    avatar_url: Optional[str] = None
    phone_number: Optional[str] = Field(default=None, max_length=30)
    job_title: Optional[str] = Field(default=None, max_length=255)
    sms_consent: Optional[bool] = None
    whatsapp_consent: Optional[bool] = None


@router.patch("/me", response_model=UserResponse)
async def update_profile(
    request: UpdateProfileRequest,
    user_id: Optional[str] = None,
) -> UserResponse:
    """Update current user's profile."""
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    from models.org_member import OrgMember

    async with get_admin_session() as session:
        user = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Update user-level fields if provided
        if request.name is not None:
            user.name = request.name
        if request.avatar_url is not None:
            user.avatar_url = request.avatar_url
        if request.phone_number is not None:
            raw_phone: str = request.phone_number.strip()
            new_phone: Optional[str] = None
            if raw_phone:
                import re as _re
                digits_only: str = _re.sub(r"[\s\-().]+", "", raw_phone)
                if not digits_only.startswith("+"):
                    digits_only = f"+1{digits_only}"
                if not _re.fullmatch(r"\+\d{10,15}", digits_only):
                    raise HTTPException(status_code=400, detail=f"Invalid phone number: must be E.164 format (e.g. +14155551234)")
                new_phone = digits_only
            if new_phone != user.phone_number:
                user.phone_number_verified_at = None
            user.phone_number = new_phone
        if request.sms_consent is not None:
            user.sms_consent = request.sms_consent
        if request.whatsapp_consent is not None:
            user.whatsapp_consent = request.whatsapp_consent

        # Update job title on org_members
        job_title: Optional[str] = None
        if user.organization_id:
            membership_result = await session.execute(
                select(OrgMember).where(
                    OrgMember.user_id == user_uuid,
                    OrgMember.organization_id == user.organization_id,
                )
            )
            membership: Optional[OrgMember] = membership_result.scalar_one_or_none()
            if membership:
                if request.job_title is not None:
                    membership.title = request.job_title or None
                job_title = membership.title

        await session.commit()
        await session.refresh(user)

        return UserResponse(
            id=str(user.id),
            email=user.email,
            name=user.name,
            role=await _get_user_role_for_active_org(session, user),
            avatar_url=user.avatar_url,
            phone_number=user.phone_number,
            job_title=job_title,
            organization_id=str(user.organization_id) if user.organization_id else None,
            sms_consent=user.sms_consent,
            whatsapp_consent=user.whatsapp_consent,
            phone_number_verified=user.phone_number_verified_at is not None,
        )


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    """Clear session."""
    return {"status": "logged out"}


@router.post("/me/request-phone-verification")
async def request_phone_verification(user_id: Optional[str] = None) -> dict[str, str | bool]:
    """Send a verification code via SMS to the current user's phone number."""
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    from services.phone_verify import request_phone_verification as send_verification

    async with get_admin_session() as session:
        user = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if not user.phone_number or not user.phone_number.strip():
            raise HTTPException(status_code=400, detail="No phone number set. Add a phone number in your profile first.")
        if user.phone_number_verified_at is not None:
            return {"status": "already_verified"}
        ok, err = await send_verification(user.phone_number.strip())
        if ok:
            return {"status": "sent"}
        raise HTTPException(status_code=502, detail=err or "Failed to send verification code")


class VerifyPhoneRequest(BaseModel):
    """Request body for verifying phone with code."""

    code: str


@router.post("/me/verify-phone")
async def verify_phone(
    request: VerifyPhoneRequest,
    user_id: Optional[str] = None,
) -> dict[str, str | bool]:
    """Verify the current user's phone number with the code sent by SMS."""
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    if not (request.code or "").strip():
        raise HTTPException(status_code=400, detail="Code is required")
    from datetime import datetime as dt
    from services.phone_verify import check_phone_verification

    async with get_admin_session() as session:
        user = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if not user.phone_number or not user.phone_number.strip():
            raise HTTPException(status_code=400, detail="No phone number set.")
        if user.phone_number_verified_at is not None:
            return {"status": "already_verified", "verified": True}
        ok, err = await check_phone_verification(user.phone_number.strip(), request.code)
        if not ok:
            raise HTTPException(status_code=400, detail=err or "Invalid or expired code")
        user.phone_number_verified_at = dt.utcnow()
        await session.commit()
        return {"status": "verified", "verified": True}


class CreateOrganizationRequest(BaseModel):
    """Request model for creating an organization."""

    id: str  # UUID from frontend
    name: str
    email_domain: str
    website_url: Optional[str] = None
    allow_duplicate_domain: bool = False  # When True, create new org even if user has one with same domain (for "create new org" flow)


class OrganizationResponse(BaseModel):
    """Response model for organization."""

    id: str
    name: str
    email_domain: Optional[str]
    logo_url: Optional[str] = None
    company_summary: Optional[str] = None
    handle: Optional[str] = None


class SyncUserRequest(BaseModel):
    """Request model for syncing a user from Supabase auth."""

    id: str  # Supabase user ID
    email: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    organization_id: Optional[str] = None


class SyncOrganizationData(BaseModel):
    """Organization data included in sync response."""

    id: str
    name: str
    logo_url: Optional[str] = None
    handle: Optional[str] = None
    subscription_required: bool = True


class SyncUserResponse(BaseModel):
    """Response model for synced user."""

    id: str
    email: str
    name: Optional[str]
    avatar_url: Optional[str]
    phone_number: Optional[str] = None
    job_title: Optional[str] = None
    organization_id: Optional[str]
    organization: Optional[SyncOrganizationData] = None
    status: str  # 'waitlist', 'invited', 'active'
    roles: list[str]  # Global roles like ['global_admin']
    needs_onboarding: bool = False
    onboarding_mode: Optional[str] = None  # "new" | "invited" | None
    sms_consent: bool = False
    whatsapp_consent: bool = False
    phone_number_verified: bool = False


@router.post("/users/sync", response_model=SyncUserResponse)
async def sync_user(request: SyncUserRequest) -> SyncUserResponse:
    """Sync a user from Supabase auth to our database.
    
    Called when a user authenticates via Supabase OAuth.
    Creates the user in our database if they don't exist.
    """
    try:
        user_uuid = UUID(request.id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    org_uuid: Optional[UUID] = None
    if request.organization_id:
        try:
            org_uuid = UUID(request.organization_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid organization ID")

    async with get_admin_session() as session:
        # Check if user already exists by ID
        existing = await session.get(User, user_uuid)
        
        # Also check by email (handles waitlist users who have a different DB ID than Supabase ID)
        # This happens when someone joins waitlist (creates user with auto-UUID) then later signs in via OAuth
        if not existing:
            result = await session.execute(
                select(User).where(User.email == request.email)
            )
            existing = result.scalar_one_or_none()
        
        if existing:
            if existing.is_guest:
                raise HTTPException(status_code=403, detail="Guest users cannot sign in")
            # Capture user's org before we update it — skip Drive sync when switching orgs (e.g. new org creation)
            user_org_before_sync: Optional[UUID] = existing.organization_id
            # If the user was found by email but has a different DB ID than the
            # Supabase ID, migrate the ID so JWT auth works without fallback.
            # This happens for waitlist/invited users who later sign in via OAuth.
            old_id: Optional[UUID] = None
            if existing.id != user_uuid:
                old_id = existing.id
                logger.warning(
                    f"User ID mismatch during sync: DB id={existing.id}, "
                    f"Supabase id={user_uuid}, email={request.email}. "
                    f"Migrating user ID to match Supabase."
                )
                # Update the user's primary key to match the Supabase ID.
                # All FK constraints referencing users.id have ON UPDATE CASCADE
                # (see migration 052), so Postgres automatically propagates the
                # PK change to every child table.
                await session.execute(
                    text("UPDATE users SET id = :new_id WHERE id = :old_id"),
                    {"new_id": str(user_uuid), "old_id": str(old_id)},
                )
                # Expire the ORM object so we re-fetch with the new PK
                await session.flush()
                session.expire(existing)
                existing = await session.get(User, user_uuid)
                if not existing:
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to migrate user ID",
                    )
                logger.info(
                    f"Successfully migrated user ID from {old_id} to {user_uuid}"
                )

            from models.org_member import OrgMember

            # Always update last_login on sync (user just logged in)
            existing.last_login = datetime.utcnow()
            
            # Update organization if provided and different
            if org_uuid and existing.organization_id != org_uuid:
                existing.organization_id = org_uuid
                # Ensure a membership exists for the new org
                existing_membership_result = await session.execute(
                    select(OrgMember).where(
                        OrgMember.user_id == existing.id,
                        OrgMember.organization_id == org_uuid,
                    )
                )
                if not existing_membership_result.scalar_one_or_none():
                    session.add(OrgMember(
                        user_id=existing.id,
                        organization_id=org_uuid,
                        role=existing.role or "member",
                        status="active",
                        joined_at=datetime.utcnow(),
                    ))
            
            # If user was invited or a CRM stub, upgrade to active on signin
            if existing.status in ("invited", "crm_only"):
                existing.status = "active"
            
            # Update avatar_url if a new one is provided (don't overwrite with null)
            if request.avatar_url and existing.avatar_url != request.avatar_url:
                existing.avatar_url = request.avatar_url
            
            # Update name if provided and different
            if request.name and existing.name != request.name:
                existing.name = request.name
            
            # Update email if user had a placeholder email
            if existing.email.endswith("@placeholder.local") and request.email:
                existing.email = request.email

            # Auto-activate any pending invitation memberships on login
            pending_result = await session.execute(
                select(OrgMember).where(
                    OrgMember.user_id == existing.id,
                    OrgMember.status == "invited",
                )
            )
            pending_memberships: list[OrgMember] = list(
                pending_result.scalars().all()
            )
            for pm in pending_memberships:
                pm.status = "onboarding"
                pm.joined_at = datetime.utcnow()
                logger.info(
                    "Auto-activated membership for user=%s org=%s on login",
                    existing.id,
                    pm.organization_id,
                )
                # If user has no active org, set this one as active
                if not existing.organization_id:
                    existing.organization_id = pm.organization_id
                    existing.role = pm.role

            org_ids_to_validate: set[UUID] = set()
            if org_uuid:
                org_ids_to_validate.add(org_uuid)
            org_ids_to_validate.update(pm.organization_id for pm in pending_memberships)
            if existing.organization_id:
                org_ids_to_validate.add(existing.organization_id)
            for candidate_org_id in org_ids_to_validate:
                await _ensure_org_has_admin(session, candidate_org_id)

            if not existing.organization_id:
                active_membership_result = await session.execute(
                    select(OrgMember)
                    .where(
                        OrgMember.user_id == existing.id,
                        OrgMember.status.in_(MEMBER_ACTIVE_STATUSES),
                    )
                    .order_by(OrgMember.joined_at.asc().nulls_last(), OrgMember.created_at.asc())
                    .limit(1)
                )
                active_membership: Optional[OrgMember] = active_membership_result.scalar_one_or_none()
                if active_membership:
                    existing.organization_id = active_membership.organization_id
                    existing.role = active_membership.role
                    logger.info(
                        "Bound user=%s to existing active membership org=%s during sync",
                        existing.id,
                        active_membership.organization_id,
                    )
            
            await session.commit()
            await session.refresh(existing)

            # Trigger a user-scoped Google Drive sync on login (throttled to >=5 min).
            # Skip when switching orgs (e.g. new org creation) — avoids flooding Celery and DB during onboarding
            is_switching_org: bool = org_uuid is not None and user_org_before_sync is not None and org_uuid != user_org_before_sync
            if existing.organization_id and not is_switching_org:
                drive_integration_result = await session.execute(
                    select(Integration).where(
                        Integration.organization_id == existing.organization_id,
                        Integration.connector == "google_drive",
                        Integration.user_id == existing.id,
                        Integration.is_active == True,
                    )
                )
                drive_integration = drive_integration_result.scalar_one_or_none()
                if drive_integration:
                    _enqueue_google_drive_login_sync(
                        existing.organization_id,
                        existing.id,
                        drive_integration,
                    )

            # Load organization data and job title if user has an org
            org_data: Optional[SyncOrganizationData] = None
            sync_job_title: Optional[str] = None
            if existing.organization_id:
                org = await session.get(Organization, existing.organization_id)
                if org:
                    _sub_ok = (org.subscription_status or "") in ("active", "trialing")
                    org_data = SyncOrganizationData(
                        id=str(org.id),
                        name=org.name,
                        logo_url=org.logo_url,
                        handle=org.handle,
                        subscription_required=not _sub_ok,
                    )
                title_result = await session.execute(
                    select(OrgMember.title).where(
                        OrgMember.user_id == existing.id,
                        OrgMember.organization_id == existing.organization_id,
                    )
                )
                sync_job_title = title_result.scalar_one_or_none()
            
            sync_needs_onboarding: bool = False
            sync_onboarding_mode: Optional[str] = None
            if existing.organization_id:
                ob_result = await session.execute(
                    select(OrgMember).where(
                        OrgMember.user_id == existing.id,
                        OrgMember.organization_id == existing.organization_id,
                        OrgMember.status == "onboarding",
                    )
                )
                ob_membership: Optional[OrgMember] = ob_result.scalar_one_or_none()
                if ob_membership:
                    sync_needs_onboarding = True
                    sync_onboarding_mode = (
                        "invited" if ob_membership.invited_by_user_id else "new"
                    )

            return SyncUserResponse(
                id=str(existing.id),
                email=existing.email,
                name=existing.name,
                avatar_url=existing.avatar_url,
                phone_number=existing.phone_number,
                job_title=sync_job_title,
                organization_id=str(existing.organization_id) if existing.organization_id else None,
                organization=org_data,
                status=existing.status,
                roles=existing.roles or [],
                needs_onboarding=sync_needs_onboarding,
                onboarding_mode=sync_onboarding_mode,
                sms_consent=existing.sms_consent,
                whatsapp_consent=existing.whatsapp_consent,
                phone_number_verified=existing.phone_number_verified_at is not None,
            )

        # Create a new active user with no org. Organization assignment is now
        # invite/membership-driven and no longer inferred from email domain.
        new_user = User(
            id=user_uuid,
            email=request.email,
            name=request.name,
            avatar_url=request.avatar_url,
            organization_id=None,
            status="active",
            role="member",
            last_login=datetime.utcnow(),
        )
        session.add(new_user)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            existing = await session.get(User, user_uuid)
            if not existing:
                existing_by_email = await session.execute(
                    select(User).where(User.email == request.email)
                )
                existing = existing_by_email.scalar_one_or_none()
            if existing:
                existing.last_login = datetime.utcnow()
                await session.commit()
                await session.refresh(existing)
                org_data_retry: Optional[SyncOrganizationData] = None
                if existing.organization_id:
                    org_retry = await session.get(Organization, existing.organization_id)
                    if org_retry:
                        _sub_ok = (org_retry.subscription_status or "") in ("active", "trialing")
                        org_data_retry = SyncOrganizationData(
                            id=str(org_retry.id),
                            name=org_retry.name,
                            logo_url=org_retry.logo_url,
                            handle=org_retry.handle,
                            subscription_required=not _sub_ok,
                        )
                return SyncUserResponse(
                    id=str(existing.id),
                    email=existing.email,
                    name=existing.name,
                    avatar_url=existing.avatar_url,
                    phone_number=existing.phone_number,
                    job_title=None,
                    organization_id=str(existing.organization_id) if existing.organization_id else None,
                    organization=org_data_retry,
                    status=existing.status,
                    roles=existing.roles or [],
                    sms_consent=existing.sms_consent,
                    whatsapp_consent=existing.whatsapp_consent,
                    phone_number_verified=existing.phone_number_verified_at is not None,
                )
            raise HTTPException(status_code=500, detail="User creation conflict; please retry")
        await session.refresh(new_user)

        return SyncUserResponse(
            id=str(new_user.id),
            email=new_user.email,
            name=new_user.name,
            avatar_url=new_user.avatar_url,
            phone_number=new_user.phone_number,
            job_title=None,
            organization_id=None,
            organization=None,
            status=new_user.status,
            roles=new_user.roles or [],
            sms_consent=new_user.sms_consent,
            whatsapp_consent=new_user.whatsapp_consent,
            phone_number_verified=new_user.phone_number_verified_at is not None,
        )


class OrgByHandleResponse(BaseModel):
    """Organization info when resolving by handle (user must have access)."""

    id: str
    name: str
    logo_url: Optional[str] = None
    handle: Optional[str] = None


@router.get("/organizations/by-handle/{handle}", response_model=OrgByHandleResponse)
async def get_organization_by_handle(
    handle: str,
    auth: AuthContext = Depends(get_current_auth),
) -> OrgByHandleResponse:
    """Get organization by handle. Returns 404 if not found or user lacks access."""
    from models.org_member import OrgMember

    user_uuid = auth.user_id

    async with get_admin_session() as session:
        result = await session.execute(
            select(Organization, OrgMember)
            .join(OrgMember, OrgMember.organization_id == Organization.id)
            .where(
                func.lower(Organization.handle) == handle.lower(),
                OrgMember.user_id == user_uuid,
                OrgMember.status.in_(MEMBER_ACTIVE_STATUSES),
            )
        )
        row = result.one_or_none()

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Organization not found or you don't have access",
            )

        org = row[0]
        return OrgByHandleResponse(
            id=str(org.id),
            name=org.name,
            logo_url=org.logo_url,
            handle=org.handle,
        )


@router.get("/organizations/by-domain/{email_domain}", response_model=OrganizationResponse)
async def get_organization_by_domain(email_domain: str) -> OrganizationResponse:
    """Get organization by email domain.
    
    Used to check if an organization exists for a domain when a new user signs up.
    """
    async with get_admin_session() as session:
        result = await session.execute(
            select(Organization)
            .where(Organization.email_domain == email_domain)
            .order_by(Organization.created_at.desc().nulls_last(), Organization.id.desc())
            .limit(1)
        )
        org = result.scalar_one_or_none()

        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        
        return OrganizationResponse(
            id=str(org.id),
            name=org.name,
            email_domain=org.email_domain,
            logo_url=org.logo_url,
            company_summary=org.company_summary,
        )


@router.post("/organizations", response_model=OrganizationResponse)
async def create_organization(
    background_tasks: BackgroundTasks,
    request: CreateOrganizationRequest,
    auth: AuthContext = Depends(get_current_auth),
) -> OrganizationResponse:
    """Create a new organization.

    Called when the first user from a company domain signs up.
    Adds the creating user as an org member so they can access workflows etc.
    """
    try:
        org_uuid = UUID(request.id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    creator_user_id: UUID | None = auth.user_id

    async with get_admin_session() as session:
        # Check if organization already exists by ID
        existing = await session.get(Organization, org_uuid)
        if existing:
            return OrganizationResponse(
                id=str(existing.id),
                name=existing.name,
                email_domain=existing.email_domain,
                logo_url=existing.logo_url,
                company_summary=existing.company_summary,
                handle=existing.handle,
            )

        # Dedup: if this user already owns an org with the same email_domain, return it
        # (Skip when allow_duplicate_domain=True — user explicitly wants a second org with same domain)
        if not request.allow_duplicate_domain and creator_user_id and request.email_domain:
            from models.org_member import OrgMember

            existing_by_domain = await session.execute(
                select(Organization)
                .join(OrgMember, OrgMember.organization_id == Organization.id)
                .where(
                    Organization.email_domain == request.email_domain,
                    OrgMember.user_id == creator_user_id,
                    OrgMember.status.in_(MEMBER_ACTIVE_STATUSES),
                )
                .limit(1)
            )
            dup: Organization | None = existing_by_domain.scalar_one_or_none()
            if dup:
                return OrganizationResponse(
                    id=str(dup.id),
                    name=dup.name,
                    email_domain=dup.email_domain,
                    logo_url=dup.logo_url,
                    company_summary=dup.company_summary,
                    handle=dup.handle,
                )

        # Create new organization with free tier auto-enrolled
        now = datetime.now(timezone.utc)
        base_handle: str = _slugify_domain(request.email_domain)
        org_handle: str = await _unique_handle(session, base_handle)
        new_org = Organization(
            id=org_uuid,
            name=request.name,
            email_domain=request.email_domain,
            website_url=(request.website_url or "").strip() or None,
            handle=org_handle,
            subscription_tier="free",
            subscription_status="active",
            credits_balance=100,
            credits_included=100,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            guest_user_enabled=False,
        )
        session.add(new_org)
        await session.flush()

        from models.org_member import OrgMember

        guest_user = User(
            email=f"guest+{org_uuid}@guest.basebase.local",
            name="Guest user",
            organization_id=org_uuid,
            status="active",
            role="member",
            is_guest=True,
        )
        session.add(guest_user)
        await session.flush()

        new_org.guest_user_id = guest_user.id
        session.add(
            OrgMember(
                user_id=guest_user.id,
                organization_id=org_uuid,
                role="member",
                status="active",
                joined_at=datetime.utcnow(),
            )
        )
        # Add the creating user as org member so they can access workflows, etc.
        if creator_user_id and creator_user_id != guest_user.id:
            session.add(
                OrgMember(
                    user_id=creator_user_id,
                    organization_id=org_uuid,
                    role="admin",
                    status="onboarding",
                    joined_at=datetime.utcnow(),
                )
            )
            # Auto-enable web_search (org-wide) so the Company Research workflow can run
            session.add(
                Integration(
                    organization_id=org_uuid,
                    connector="web_search",
                    user_id=creator_user_id,
                    scope="organization",
                    nango_connection_id="builtin",
                    connected_by_user_id=creator_user_id,
                    is_active=True,
                    share_synced_data=True,
                    share_query_access=True,
                    share_write_access=True,
                    pending_sharing_config=False,
                )
            )
            # Auto-enable artifacts so the agent can create and update downloadable files
            session.add(
                Integration(
                    organization_id=org_uuid,
                    connector="artifacts",
                    user_id=creator_user_id,
                    scope="organization",
                    nango_connection_id="builtin",
                    connected_by_user_id=creator_user_id,
                    is_active=True,
                    share_synced_data=True,
                    share_query_access=True,
                    share_write_access=True,
                    pending_sharing_config=False,
                )
            )
            # Auto-enable apps so the agent can create and update interactive mini-apps
            session.add(
                Integration(
                    organization_id=org_uuid,
                    connector="apps",
                    user_id=creator_user_id,
                    scope="organization",
                    nango_connection_id="builtin",
                    connected_by_user_id=creator_user_id,
                    is_active=True,
                    share_synced_data=True,
                    share_query_access=True,
                    share_write_access=True,
                    pending_sharing_config=False,
                )
            )

        await session.commit()
        await session.refresh(new_org)

        # Create onboarding Company Research workflow (trigger_data: website_url, organization_id, organization_name)
        from models.workflow import Workflow

        research_workflow = Workflow(
            organization_id=org_uuid,
            created_by_user_id=guest_user.id,
            name="Company Research",
            description="Onboarding: fetch website and web search to summarize the company.",
            trigger_type="manual",
            trigger_config={},
            steps=[],
            prompt=(
                "You are researching a company for onboarding. Use the provided input parameters.\n\n"
                "1. If website_url is provided: call run_action with provider='web_search', action='fetch_url', "
                "params={url: website_url, render_js: true} to read the site content.\n"
                "2. Call query_system with system='web_search' to search for the company (use organization_name in the query).\n"
                "3. Write a concise 2–3 sentence summary of what the company does, its industry, and notable aspects.\n"
                "4. Call run_sql_write with: UPDATE organizations SET company_summary = '<your summary>' WHERE id = '<organization_id>'"
            ),
            auto_approve_tools=["run_on_connector", "query_on_connector", "run_sql_write"],
            input_schema={
                "type": "object",
                "properties": {
                    "website_url": {"type": "string", "description": "Company website URL to fetch"},
                    "organization_id": {"type": "string", "format": "uuid", "description": "Organization UUID"},
                    "organization_name": {"type": "string", "description": "Company name for web search"},
                },
                "required": ["organization_id", "organization_name"],
            },
            is_enabled=True,
        )
        session.add(research_workflow)
        await session.commit()

        website_url_trimmed: str | None = (request.website_url or "").strip() or None
        if website_url_trimmed:
            logger.info("[auth] Queuing favicon fetch for org %s from %s", org_uuid, website_url_trimmed)
            background_tasks.add_task(update_org_logo_from_website, org_uuid, website_url_trimmed)

        return OrganizationResponse(
            id=str(new_org.id),
            name=new_org.name,
            email_domain=new_org.email_domain,
            logo_url=new_org.logo_url,
            company_summary=new_org.company_summary,
            handle=new_org.handle,
        )


@router.post("/organizations/{org_id}/complete-onboarding")
async def complete_onboarding(
    org_id: str,
    auth: AuthContext = Depends(get_current_auth),
) -> dict[str, str]:
    """Flip the caller's membership from 'onboarding' to 'active'."""
    from models.org_member import OrgMember

    try:
        org_uuid: UUID = UUID(org_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    user_uuid: UUID = auth.user_id

    async with get_admin_session() as session:
        result = await session.execute(
            select(OrgMember).where(
                OrgMember.user_id == user_uuid,
                OrgMember.organization_id == org_uuid,
                OrgMember.status == "onboarding",
            )
        )
        membership: Optional[OrgMember] = result.scalar_one_or_none()
        if not membership:
            raise HTTPException(
                status_code=404,
                detail="No onboarding membership found for this organization",
            )

        membership.status = "active"
        await session.commit()

    return {"status": "ok"}


class IdentityMappingResponse(BaseModel):
    """A single external identity mapping."""

    id: str
    source: str  # 'slack', 'hubspot', 'salesforce', ...
    external_userid: Optional[str]
    external_email: Optional[str]
    match_source: str
    updated_at: Optional[str]


class TeamMemberResponse(BaseModel):
    """Response model for a team member."""

    id: str
    name: Optional[str]
    email: str
    role: Optional[str]
    avatar_url: Optional[str]
    job_title: Optional[str] = None
    status: Optional[str] = None  # 'active', 'crm_only', etc.
    is_guest: bool = False
    can_login_as_admin: bool = False  # True when user is org admin for this org, or global_admin
    identities: list[IdentityMappingResponse] = []


class TeamMembersListResponse(BaseModel):
    """Response model for list of team members."""

    members: list[TeamMemberResponse]
    unmapped_identities: list[IdentityMappingResponse] = []
    guest_user_enabled: bool = False


class LinkIdentityRequest(BaseModel):
    """Request to manually link a user to an external identity."""

    target_user_id: str
    mapping_id: str


class UnlinkIdentityRequest(BaseModel):
    """Request to unlink an external identity mapping from a user."""

    mapping_id: str


@router.get("/organizations/{org_id}/members", response_model=TeamMembersListResponse)
async def get_organization_members(
    org_id: str,
    auth: AuthContext = Depends(get_current_auth),
) -> TeamMembersListResponse:
    """Get all team members for an organization, including identity mappings.

    Only accessible by members of that organization.
    Uses JWT to identify the requester.
    """
    from models.external_identity_mapping import ExternalIdentityMapping
    from models.org_member import OrgMember

    try:
        org_uuid = UUID(org_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID format")

    user_uuid = auth.user_id

    # Use admin session so we can join across users + memberships without RLS issues
    async with get_admin_session() as session:
        # Verify requesting user has an active membership in this org
        requester_check = await session.execute(
            select(OrgMember).where(
                OrgMember.user_id == user_uuid,
                OrgMember.organization_id == org_uuid,
                OrgMember.status.in_(MEMBER_ACTIVE_STATUSES),
            )
        )
        if not requester_check.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Not authorized to view this organization's members")

        # Load org so the response can still report guest-user toggle state.
        org = await session.get(Organization, org_uuid)
        guest_user_enabled: bool = bool(org and org.guest_user_enabled)

        # Fetch members via memberships (active and invited)
        membership_result = await session.execute(
            select(User, OrgMember)
            .join(OrgMember, User.id == OrgMember.user_id)
            .where(
                OrgMember.organization_id == org_uuid,
                OrgMember.status.in_(["active", "onboarding", "invited"]),
            )
        )
        member_rows = membership_result.all()
        users: list[User] = [row[0] for row in member_rows]
        membership_by_user: dict[UUID, OrgMember] = {
            row[1].user_id: row[1] for row in member_rows
        }

        # Fetch all identity mappings for this org in one query
        mappings_result = await session.execute(
            select(ExternalIdentityMapping).where(
                ExternalIdentityMapping.organization_id == org_uuid,
            )
        )
        all_mappings: list[ExternalIdentityMapping] = list(mappings_result.scalars().all())

        # Group mappings by user_id
        mappings_by_user: dict[UUID | None, list[ExternalIdentityMapping]] = {}
        for m in all_mappings:
            mappings_by_user.setdefault(m.user_id, []).append(m)

        members: list[TeamMemberResponse] = []
        for u in users:
            # Skip crm_only stub users entirely — they shouldn't appear in the team list
            if u.status == "crm_only":
                continue
            user_mappings: list[ExternalIdentityMapping] = mappings_by_user.get(u.id, [])
            identities: list[IdentityMappingResponse] = [
                IdentityMappingResponse(
                    id=str(m.id),
                    source=m.source,
                    external_userid=m.external_userid,
                    external_email=m.external_email,
                    match_source=m.match_source,
                    updated_at=m.updated_at.isoformat() if m.updated_at else None,
                )
                for m in user_mappings
            ]
            membership: OrgMember | None = membership_by_user.get(u.id)
            member_status: str = membership.status if membership else (u.status or "active")
            members.append(
                TeamMemberResponse(
                    id=str(u.id),
                    name=u.name,
                    email=u.email,
                    role=membership.role if membership else "member",
                    avatar_url=u.avatar_url,
                    job_title=membership.title if membership else None,
                    status=member_status,
                    is_guest=u.is_guest,
                    can_login_as_admin=(
                        bool(membership and membership.role == "admin")
                        or _is_global_admin(u)
                    ),
                    identities=identities,
                )
            )

        # Keep guest user pinned to the top of team lists, then sort remaining
        # members alphabetically so all consumers (including sidebar/panels)
        # get a consistent order without duplicating sort rules in clients.
        members.sort(
            key=lambda member: (
                not member.is_guest,
                (member.name or member.email).lower(),
            )
        )

        # Collect unmapped identity rows (user_id is NULL)
        unmapped_mappings: list[ExternalIdentityMapping] = mappings_by_user.get(None, [])

        # Avoid showing stale "unmapped" rows when the same external account
        # is already linked to a team user via the same source + external identity.
        # Keep manually unlinked identities visible so admins can relink them.
        linked_identity_keys: set[tuple[str, str]] = set()
        for mapping in all_mappings:
            if mapping.user_id is None:
                continue
            identity_value = mapping.external_email or mapping.external_userid
            if identity_value:
                linked_identity_keys.add((mapping.source, identity_value.lower()))

        filtered_unmapped_mappings: list[ExternalIdentityMapping] = []
        for mapping in unmapped_mappings:
            identity_value = mapping.external_email or mapping.external_userid
            if not identity_value:
                filtered_unmapped_mappings.append(mapping)
                continue
            if mapping.match_source == "manual_unlink":
                logger.info(
                    "Keeping manually unlinked identity id=%s org=%s source=%s identity=%s visible for relinking",
                    mapping.id,
                    org_id,
                    mapping.source,
                    identity_value,
                )
                filtered_unmapped_mappings.append(mapping)
                continue
            if (mapping.source, identity_value.lower()) in linked_identity_keys:
                logger.info(
                    "Skipping unmapped identity id=%s org=%s source=%s identity=%s because it is already linked",
                    mapping.id,
                    org_id,
                    mapping.source,
                    identity_value,
                )
                continue
            filtered_unmapped_mappings.append(mapping)

        unmapped_identities: list[IdentityMappingResponse] = [
            IdentityMappingResponse(
                id=str(m.id),
                source=m.source,
                external_userid=m.external_userid,
                external_email=m.external_email,
                match_source=m.match_source,
                updated_at=m.updated_at.isoformat() if m.updated_at else None,
            )
            for m in filtered_unmapped_mappings
        ]

        return TeamMembersListResponse(
            members=members,
            unmapped_identities=unmapped_identities,
            guest_user_enabled=guest_user_enabled,
        )


@router.post("/organizations/{org_id}/members/link-identity")
async def link_identity(
    org_id: str,
    request: LinkIdentityRequest,
    user_id: Optional[str] = None,
) -> dict[str, str]:
    """Manually link an unmatched identity mapping to a user.

    Reassigns the mapping's ``user_id`` and ``revtops_email`` to the target user.
    """
    from models.external_identity_mapping import ExternalIdentityMapping

    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        org_uuid = UUID(org_id)
        target_uuid = UUID(request.target_user_id)
        mapping_uuid = UUID(request.mapping_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session() as session:
        # Verify target user belongs to this org
        target_user: User | None = await session.get(User, target_uuid)
        if not target_user or target_user.organization_id != org_uuid:
            raise HTTPException(status_code=404, detail="Target user not found in this organization")
        if getattr(target_user, "is_guest", False):
            logger.warning(
                "Blocked manual identity link to guest user org=%s target_user=%s mapping=%s by_user=%s",
                org_uuid,
                target_uuid,
                mapping_uuid,
                user_id,
            )
            raise HTTPException(status_code=403, detail="Guest user identities cannot be manually linked")

        # Fetch the mapping
        mapping: ExternalIdentityMapping | None = await session.get(ExternalIdentityMapping, mapping_uuid)
        if not mapping or mapping.organization_id != org_uuid:
            raise HTTPException(status_code=404, detail="Identity mapping not found")

        logger.info(
            "Linking identity mapping id=%s org=%s target_user=%s source=%s external_userid=%s external_email=%s",
            mapping_uuid,
            org_uuid,
            target_uuid,
            mapping.source,
            mapping.external_userid,
            mapping.external_email,
        )

        # Set user_id on the mapping
        mapping.user_id = target_uuid
        mapping.revtops_email = target_user.email
        if mapping.source == "slack" and not mapping.external_email and target_user.email:
            mapping.external_email = target_user.email
        mapping.match_source = "admin_manual_link"

        # Slack identities can appear as separate rows (Slack user id vs email-derived).
        # Link related unmapped rows as well so both Slack UI and email views stay in sync.
        if mapping.source == "slack":
            related_filters = []
            if mapping.external_userid:
                related_filters.append(ExternalIdentityMapping.external_userid == mapping.external_userid)
            if mapping.external_email:
                related_filters.append(func.lower(ExternalIdentityMapping.external_email) == mapping.external_email.lower())
            if target_user.email:
                related_filters.append(func.lower(ExternalIdentityMapping.external_email) == target_user.email.lower())

            if related_filters:
                related_result = await session.execute(
                    select(ExternalIdentityMapping)
                    .where(ExternalIdentityMapping.organization_id == org_uuid)
                    .where(ExternalIdentityMapping.source == "slack")
                    .where(ExternalIdentityMapping.id != mapping_uuid)
                    .where(ExternalIdentityMapping.user_id.is_(None))
                    .where(or_(*related_filters))
                )
                related_mappings: list[ExternalIdentityMapping] = list(related_result.scalars().all())
                for related_mapping in related_mappings:
                    related_mapping.user_id = target_uuid
                    related_mapping.revtops_email = target_user.email
                    if not related_mapping.external_email and target_user.email:
                        related_mapping.external_email = target_user.email
                    related_mapping.match_source = "admin_manual_link"

                if related_mappings:
                    logger.info(
                        "Linked %d additional Slack mappings org=%s target_user=%s seed_mapping=%s",
                        len(related_mappings),
                        org_uuid,
                        target_uuid,
                        mapping_uuid,
                    )

        await session.commit()

    return {"status": "linked"}


@router.post("/organizations/{org_id}/members/unlink-identity")
async def unlink_identity(
    org_id: str,
    request: UnlinkIdentityRequest,
    user_id: Optional[str] = None,
) -> dict[str, str]:
    """Unlink an identity mapping from a user in the org.

    Access rules:
    - Users can always unlink identities currently linked to themselves.
    - Users with link-identity permission can unlink any identity in the org.
    """
    from models.external_identity_mapping import ExternalIdentityMapping

    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        org_uuid = UUID(org_id)
        requester_uuid = UUID(user_id)
        mapping_uuid = UUID(request.mapping_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session() as session:
        requester: User | None = await session.get(User, requester_uuid)
        if not requester or requester.organization_id != org_uuid:
            raise HTTPException(status_code=403, detail="Not authorized to modify this organization")

        mapping: ExternalIdentityMapping | None = await session.get(ExternalIdentityMapping, mapping_uuid)
        if not mapping or mapping.organization_id != org_uuid:
            raise HTTPException(status_code=404, detail="Identity mapping not found")

        if mapping.user_id:
            linked_user: User | None = await session.get(User, mapping.user_id)
            if linked_user and getattr(linked_user, "is_guest", False):
                logger.warning(
                    "Blocked unlink attempt for guest identity mapping id=%s org=%s by_user=%s",
                    mapping_uuid,
                    org_uuid,
                    requester_uuid,
                )
                raise HTTPException(status_code=403, detail="Guest user identities cannot be unlinked")

        is_unlinking_own_identity = mapping.user_id == requester_uuid
        can_link_identities_in_org = True  # Mirrors current link-identity access for org members.
        if not is_unlinking_own_identity and not can_link_identities_in_org:
            raise HTTPException(status_code=403, detail="Not authorized to unlink this identity")

        mapping.user_id = None
        mapping.revtops_email = None
        mapping.match_source = "manual_unlink"
        await session.commit()

        logger.info(
            "Unlinked identity mapping id=%s org=%s by_user=%s own_identity=%s",
            mapping_uuid,
            org_uuid,
            requester_uuid,
            is_unlinking_own_identity,
        )

    return {"status": "unlinked"}


# =============================================================================
# Multi-Org Membership Endpoints
# =============================================================================


class InviteToOrgRequest(BaseModel):
    """Request model for inviting a user to an organization."""

    email: str
    role: str = "member"
    name: Optional[str] = None


class InviteToOrgResponse(BaseModel):
    """Response model for org invitation."""

    membership_id: str
    user_id: str
    email: str
    status: str


class SlackMissingInviteRequest(BaseModel):
    """Bulk invite Slack users missing from this org."""

    dry_run: bool = True
    confirm_large_invite: bool = False


class SlackMissingInviteResponse(BaseModel):
    """Summary of Slack bulk invite attempt/preview."""

    total_slack_users_with_email: int
    already_in_org: int
    missing_users: int
    invited_count: int
    requires_confirmation: bool
    invited_emails: list[str]


@router.post("/organizations/{org_id}/invitations", response_model=InviteToOrgResponse)
async def invite_to_organization(
    org_id: str,
    request: InviteToOrgRequest,
    background_tasks: BackgroundTasks,
    user_id: Optional[str] = None,
) -> InviteToOrgResponse:
    """Invite a user to an organization by email.

    Creates a membership with status='invited'. If the user doesn't exist
    yet, creates a stub user with status='invited'. Sends an invitation email.
    """
    from models.org_member import OrgMember
    from services.email import send_org_invitation_email

    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        org_uuid = UUID(org_id)
        inviter_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    invite_email: str = request.email.strip().lower()
    if not invite_email or "@" not in invite_email:
        raise HTTPException(status_code=400, detail="Invalid email address")

    async with get_admin_session() as session:
        # Verify inviter and load org
        inviter: Optional[User] = await session.get(User, inviter_uuid)
        if not inviter:
            raise HTTPException(status_code=403, detail="Not authorized")

        org: Optional[Organization] = await session.get(Organization, org_uuid)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Require inviter to be an active member, or be a global admin (e.g. Admin Panel inviting to any org)
        is_global_admin: bool = (
            inviter.role == "global_admin" or bool(inviter.roles and "global_admin" in inviter.roles)
        )
        if not is_global_admin:
            result = await session.execute(
                select(OrgMember).where(
                    OrgMember.user_id == inviter_uuid,
                    OrgMember.organization_id == org_uuid,
                    OrgMember.status.in_(MEMBER_ACTIVE_STATUSES),
                )
            )
            inviter_membership: Optional[OrgMember] = result.scalar_one_or_none()
            if not inviter_membership:
                raise HTTPException(status_code=403, detail="Not a member of this organization")

        # Find or create the target user
        result = await session.execute(
            select(User).where(User.email == invite_email)
        )
        target_user: Optional[User] = result.scalar_one_or_none()

        if not target_user:
            # Create stub user
            invite_name: Optional[str] = (request.name or "").strip() or None
            target_user = User(
                email=invite_email,
                name=invite_name,
                status="invited",
                role="member",
                invited_at=datetime.utcnow(),
            )
            session.add(target_user)
            await session.flush()

        # Check for existing membership
        result = await session.execute(
            select(OrgMember).where(
                OrgMember.user_id == target_user.id,
                OrgMember.organization_id == org_uuid,
            )
        )
        existing_membership: Optional[OrgMember] = result.scalar_one_or_none()

        if existing_membership:
            if existing_membership.status == "active":
                raise HTTPException(
                    status_code=409,
                    detail="User is already a member of this organization",
                )
            if existing_membership.status == "invited":
                existing_membership.invited_by_user_id = inviter_uuid
                existing_membership.invited_at = datetime.utcnow()
                await session.commit()
                membership_id_str = str(existing_membership.id)
                inviter_name = inviter.name or inviter.email
                background_tasks.add_task(
                    send_org_invitation_email,
                    invite_email,
                    org.name,
                    inviter_name,
                    org_logo_url=org.logo_url,
                    inviter_avatar_url=inviter.avatar_url,
                )
                return InviteToOrgResponse(
                    membership_id=membership_id_str,
                    user_id=str(target_user.id),
                    email=invite_email,
                    status="invited",
                )
            # Re-invite a deactivated member
            existing_membership.status = "invited"
            existing_membership.invited_by_user_id = inviter_uuid
            existing_membership.invited_at = datetime.utcnow()
            existing_membership.role = request.role
            await session.commit()
            membership_id_str: str = str(existing_membership.id)
        else:
            new_membership = OrgMember(
                user_id=target_user.id,
                organization_id=org_uuid,
                role=request.role,
                status="invited",
                invited_by_user_id=inviter_uuid,
                invited_at=datetime.utcnow(),
            )
            session.add(new_membership)
            await session.commit()
            membership_id_str = str(new_membership.id)

        # Send invitation email in background
        inviter_name: str = inviter.name or inviter.email
        background_tasks.add_task(
            send_org_invitation_email,
            invite_email,
            org.name,
            inviter_name,
            org_logo_url=org.logo_url,
            inviter_avatar_url=inviter.avatar_url,
        )

        return InviteToOrgResponse(
            membership_id=membership_id_str,
            user_id=str(target_user.id),
            email=invite_email,
            status="invited",
        )


@router.post(
    "/organizations/{org_id}/invitations/slack-missing",
    response_model=SlackMissingInviteResponse,
)
async def invite_missing_slack_users_to_organization(
    org_id: str,
    request: SlackMissingInviteRequest,
    background_tasks: BackgroundTasks,
    user_id: Optional[str] = None,
) -> SlackMissingInviteResponse:
    """Invite Slack users (with email) who are not already present in the org."""
    from models.org_member import OrgMember
    from models.external_identity_mapping import ExternalIdentityMapping
    from services.email import send_org_invitation_email

    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        org_uuid = UUID(org_id)
        inviter_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_admin_session() as session:
        inviter: Optional[User] = await session.get(User, inviter_uuid)
        if not inviter:
            raise HTTPException(status_code=403, detail="Not authorized")

        org: Optional[Organization] = await session.get(Organization, org_uuid)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        if not _is_global_admin(inviter):
            inviter_membership = await _get_org_membership(session, inviter_uuid, org_uuid)
            if not inviter_membership:
                raise HTTPException(status_code=403, detail="Not a member of this organization")

        slack_integration_result = await session.execute(
            select(Integration.id).where(
                Integration.organization_id == org_uuid,
                Integration.connector == "slack",
                Integration.is_active == True,
            )
        )
        if not slack_integration_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Slack integration not connected")

        slack_rows = await session.execute(
            select(ExternalIdentityMapping.external_email)
            .where(
                ExternalIdentityMapping.organization_id == org_uuid,
                ExternalIdentityMapping.source == "slack",
                ExternalIdentityMapping.external_email.is_not(None),
            )
            .distinct()
        )
        raw_emails = [row[0] for row in slack_rows.all()]
        slack_emails: set[str] = {
            str(email).strip().lower()
            for email in raw_emails
            if isinstance(email, str) and "@" in email
        }

        memberships_result = await session.execute(
            select(User.email)
            .join(OrgMember, OrgMember.user_id == User.id)
            .where(
                OrgMember.organization_id == org_uuid,
                User.email.is_not(None),
            )
        )
        existing_org_emails: set[str] = {
            str(row[0]).strip().lower()
            for row in memberships_result.all()
            if isinstance(row[0], str)
        }

        missing_emails = sorted(slack_emails - existing_org_emails)
        requires_confirmation = len(missing_emails) > 10

        if request.dry_run:
            return SlackMissingInviteResponse(
                total_slack_users_with_email=len(slack_emails),
                already_in_org=len(slack_emails & existing_org_emails),
                missing_users=len(missing_emails),
                invited_count=0,
                requires_confirmation=requires_confirmation,
                invited_emails=[],
            )

        if requires_confirmation and not request.confirm_large_invite:
            raise HTTPException(
                status_code=400,
                detail="Large invite requires explicit confirmation",
            )

        if not missing_emails:
            return SlackMissingInviteResponse(
                total_slack_users_with_email=len(slack_emails),
                already_in_org=len(slack_emails & existing_org_emails),
                missing_users=0,
                invited_count=0,
                requires_confirmation=False,
                invited_emails=[],
            )

        target_users_result = await session.execute(
            select(User).where(User.email.in_(missing_emails))
        )
        target_users = {user.email.lower(): user for user in target_users_result.scalars().all() if user.email}

        invited_emails: list[str] = []
        for email in missing_emails:
            target_user = target_users.get(email)
            if not target_user:
                target_user = User(
                    email=email,
                    name=None,
                    status="invited",
                    role="member",
                    invited_at=datetime.utcnow(),
                )
                session.add(target_user)
                await session.flush()
                target_users[email] = target_user

            membership_result = await session.execute(
                select(OrgMember).where(
                    OrgMember.user_id == target_user.id,
                    OrgMember.organization_id == org_uuid,
                )
            )
            existing_membership: Optional[OrgMember] = membership_result.scalar_one_or_none()
            if existing_membership:
                continue

            session.add(
                OrgMember(
                    user_id=target_user.id,
                    organization_id=org_uuid,
                    role="member",
                    status="invited",
                    invited_by_user_id=inviter_uuid,
                    invited_at=datetime.utcnow(),
                )
            )
            invited_emails.append(email)

        await session.commit()

        inviter_name: str = inviter.name or inviter.email
        for email in invited_emails:
            background_tasks.add_task(
                send_org_invitation_email,
                email,
                org.name,
                inviter_name,
                org_logo_url=org.logo_url,
                inviter_avatar_url=inviter.avatar_url,
            )

        return SlackMissingInviteResponse(
            total_slack_users_with_email=len(slack_emails),
            already_in_org=len(slack_emails & existing_org_emails),
            missing_users=len(missing_emails),
            invited_count=len(invited_emails),
            requires_confirmation=False,
            invited_emails=invited_emails,
        )


class UserOrganizationResponse(BaseModel):
    """A single organization the user belongs to."""

    id: str
    name: str
    logo_url: Optional[str] = None
    handle: Optional[str] = None
    role: str
    is_active: bool


class UserOrganizationsListResponse(BaseModel):
    """List of organizations the current user belongs to."""

    organizations: list[UserOrganizationResponse]


@router.get("/users/me/organizations", response_model=UserOrganizationsListResponse)
async def list_user_organizations(
    auth: AuthContext = Depends(get_current_auth),
) -> UserOrganizationsListResponse:
    """List all organizations the authenticated user belongs to (from JWT)."""
    from models.org_member import OrgMember

    user_uuid = auth.user_id

    # Cross-org query — must bypass RLS
    async with get_admin_session() as session:
        user: Optional[User] = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        result = await session.execute(
            select(OrgMember, Organization)
            .join(Organization, OrgMember.organization_id == Organization.id)
            .where(
                OrgMember.user_id == user_uuid,
                OrgMember.status.in_(MEMBER_ACTIVE_STATUSES),
            )
        )
        rows = result.all()

        orgs: list[UserOrganizationResponse] = [
            UserOrganizationResponse(
                id=str(org.id),
                name=org.name,
                logo_url=org.logo_url,
                handle=org.handle,
                role=membership.role,
                is_active=(user.organization_id == org.id),
            )
            for membership, org in rows
        ]

        return UserOrganizationsListResponse(organizations=orgs)


class SwitchActiveOrgRequest(BaseModel):
    """Request to switch the user's active organization."""

    organization_id: str


@router.patch("/users/me/active-organization", response_model=SyncUserResponse)
async def switch_active_organization(
    request: SwitchActiveOrgRequest,
    auth: AuthContext = Depends(get_current_auth),
) -> SyncUserResponse:
    """Switch the user's active organization.

    Validates that the user (from JWT) has an active membership in the target org,
    then updates User.organization_id.
    """
    from models.org_member import OrgMember

    try:
        user_uuid = auth.user_id
        target_org_uuid = UUID(request.organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID format")

    async with get_admin_session() as session:
        user: Optional[User] = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Validate membership
        result = await session.execute(
            select(OrgMember).where(
                OrgMember.user_id == user_uuid,
                OrgMember.organization_id == target_org_uuid,
                OrgMember.status.in_(MEMBER_ACTIVE_STATUSES),
            )
        )
        membership: Optional[OrgMember] = result.scalar_one_or_none()
        if not membership:
            raise HTTPException(
                status_code=403,
                detail="You are not an active member of this organization",
            )

        # Update active org
        user.organization_id = target_org_uuid
        await session.commit()
        await session.refresh(user)

        # Load org data
        org: Optional[Organization] = await session.get(Organization, target_org_uuid)
        org_data: Optional[SyncOrganizationData] = None
        if org:
            _sub_ok = (org.subscription_status or "") in ("active", "trialing")
            org_data = SyncOrganizationData(
                id=str(org.id),
                name=org.name,
                logo_url=org.logo_url,
                handle=org.handle,
                subscription_required=not _sub_ok,
            )

        return SyncUserResponse(
            id=str(user.id),
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            phone_number=user.phone_number,
            job_title=membership.title if membership else None,
            organization_id=str(user.organization_id) if user.organization_id else None,
            organization=org_data,
            status=user.status,
            roles=user.roles or [],
            sms_consent=user.sms_consent,
            whatsapp_consent=user.whatsapp_consent,
            phone_number_verified=user.phone_number_verified_at is not None,
        )


class UpdateMemberRoleRequest(BaseModel):
    """Update a member's org-scoped role."""

    role: str


@router.patch("/organizations/{org_id}/members/{target_user_id}/role")
async def update_organization_member_role(
    org_id: str,
    target_user_id: str,
    request: UpdateMemberRoleRequest,
    user_id: Optional[str] = None,
) -> dict[str, str]:
    """Promote or demote a member. Requires org admin for this org, or global_admin."""
    from models.org_member import OrgMember

    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if request.role not in {"admin", "member"}:
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'member'")

    try:
        org_uuid = UUID(org_id)
        target_uuid = UUID(target_user_id)
        requester_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_admin_session() as session:
        requester: Optional[User] = await session.get(User, requester_uuid)
        if not await _can_administer_org(session, requester, org_uuid):
            raise HTTPException(status_code=403, detail="Org admin or global_admin required for this organization")

        membership_result = await session.execute(
            select(OrgMember).where(
                OrgMember.user_id == target_uuid,
                OrgMember.organization_id == org_uuid,
                OrgMember.status.in_(MEMBER_ACTIVE_STATUSES),
            )
        )
        target_membership: Optional[OrgMember] = membership_result.scalar_one_or_none()
        if not target_membership:
            raise HTTPException(status_code=404, detail="Active member not found")

        target_membership.role = request.role

        await _ensure_org_has_admin(session, org_uuid)
        await session.commit()

        logger.info(
            "Updated org member role org=%s target_user=%s new_role=%s by_user=%s",
            org_uuid,
            target_uuid,
            request.role,
            requester_uuid,
        )

    return {"status": "updated", "role": request.role}

@router.delete("/organizations/{org_id}/members/{target_user_id}")
async def remove_organization_member(
    org_id: str,
    target_user_id: str,
    user_id: Optional[str] = None,
) -> dict[str, str]:
    """Remove a member from an organization, and unlink all identities.

    Requires org admin for this org, or global_admin.
    """
    from models.org_member import OrgMember
    from models.external_identity_mapping import ExternalIdentityMapping

    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        org_uuid = UUID(org_id)
        target_uuid = UUID(target_user_id)
        requester_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_admin_session() as session:
        requester: Optional[User] = await session.get(User, requester_uuid)
        if not await _can_administer_org(session, requester, org_uuid):
            raise HTTPException(status_code=403, detail="Org admin or global_admin required for this organization")

        result = await session.execute(
            select(OrgMember).where(
                OrgMember.user_id == target_uuid,
                OrgMember.organization_id == org_uuid,
                OrgMember.status.in_(["active", "onboarding", "invited"]),
            )
        )
        target_membership: Optional[OrgMember] = result.scalar_one_or_none()
        if not target_membership:
            raise HTTPException(status_code=404, detail="Member not found")

        target_user: Optional[User] = await session.get(User, target_uuid)
        if target_user and getattr(target_user, "is_guest", False):
            logger.warning(
                "Blocked delete attempt for guest user org=%s target_user=%s by_user=%s",
                org_uuid,
                target_uuid,
                requester_uuid,
            )
            raise HTTPException(status_code=403, detail="Guest user cannot be deleted")

        target_membership.status = "deactivated"

        unlink_result = await session.execute(
            select(ExternalIdentityMapping).where(
                ExternalIdentityMapping.organization_id == org_uuid,
                ExternalIdentityMapping.user_id == target_uuid,
            )
        )
        mappings_to_unlink = list(unlink_result.scalars().all())
        for mapping in mappings_to_unlink:
            mapping.user_id = None
            mapping.revtops_email = None
            mapping.match_source = "manual_unlink"

        if target_user and target_user.organization_id == org_uuid:
            target_user.organization_id = None

        await _ensure_org_has_admin(session, org_uuid)
        await session.commit()

        logger.info(
            "Removed org member org=%s target_user=%s by_user=%s unlinked_identities=%d",
            org_uuid,
            target_uuid,
            requester_uuid,
            len(mappings_to_unlink),
        )

    return {"status": "removed"}


class UpdateOrganizationRequest(BaseModel):
    """Request model for updating organization settings."""

    name: Optional[str] = None
    logo_url: Optional[str] = None


class UpdateGuestUserRequest(BaseModel):
    """Request model for enabling/disabling guest-user fallback."""

    enabled: bool


@router.patch("/organizations/{org_id}", response_model=OrganizationResponse)
async def update_organization(
    org_id: str,
    request: UpdateOrganizationRequest,
    user_id: Optional[str] = None,
) -> OrganizationResponse:
    """Update organization settings.

    Requires org admin for this organization, or global_admin.
    """
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        org_uuid = UUID(org_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session() as session:
        requesting_user = await session.get(User, user_uuid)
        if not await _can_administer_org(session, requesting_user, org_uuid):
            raise HTTPException(status_code=403, detail="Org admin or global_admin required for this organization")

        # Fetch and update organization
        org = await session.get(Organization, org_uuid)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Update fields if provided
        if request.name is not None:
            org.name = request.name
        if request.logo_url is not None:
            org.logo_url = request.logo_url

        await session.commit()
        await session.refresh(org)

        return OrganizationResponse(
            id=str(org.id),
            name=org.name,
            email_domain=org.email_domain,
            logo_url=org.logo_url,
            company_summary=org.company_summary,
        )


@router.get("/organizations/{org_id}", response_model=OrganizationResponse)
async def get_organization(
    org_id: str,
    auth: AuthContext = Depends(get_current_auth),
    user_id: Optional[str] = None,
) -> OrganizationResponse:
    """Get organization details. Requires org membership."""
    from models.org_member import OrgMember

    uid = user_id or (str(auth.user_id) if auth.user_id else None)
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        org_uuid = UUID(org_id)
        user_uuid = UUID(uid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_admin_session() as session:
        # Verify user is member of this org
        membership = await session.execute(
            select(OrgMember).where(
                OrgMember.organization_id == org_uuid,
                OrgMember.user_id == user_uuid,
                OrgMember.status.in_(MEMBER_ACTIVE_STATUSES),
            )
        )
        if not membership.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Not a member of this organization")

        org = await session.get(Organization, org_uuid)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        return OrganizationResponse(
            id=str(org.id),
            name=org.name,
            email_domain=org.email_domain,
            logo_url=org.logo_url,
            company_summary=org.company_summary,
        )


@router.delete("/organizations/{org_id}")
async def delete_organization(
    org_id: str,
    auth: AuthContext = Depends(get_current_auth),
) -> dict[str, str]:
    """Delete an organization and all org-scoped records.

    Only organization admins (or global admins) may perform this action.
    """
    try:
        org_uuid = UUID(org_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_admin_session() as session:
        requesting_user: Optional[User] = await session.get(User, auth.user_id)
        if not await _can_administer_org(session, requesting_user, org_uuid):
            raise HTTPException(status_code=403, detail="You can't do that. Only organization admins can delete organizations.")

        org: Optional[Organization] = await session.get(Organization, org_uuid)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        logger.warning("Deleting organization org=%s requested_by=%s", org_uuid, auth.user_id)

        # Delete org-scoped records first (before touching users) in dependency order.
        org_scoped_tables: tuple[str, ...] = (
            "user_mappings_for_identity",
            "messenger_user_mappings",
            "messenger_bot_installs",
            "shared_files",
            "credit_transactions",
            "change_sessions",
            "chat_messages",
            "conversations",
            "activities",
            "crm_operations",
            "accounts",
            "contacts",
            "deals",
            "goals",
            "pipelines",
            "integrations",
            "github_pull_requests",
            "github_commits",
            "github_repositories",
            "tracker_issues",
            "tracker_projects",
            "tracker_teams",
            "meetings",
            "workflow_runs",
            "workflows",
            "pending_operations",
            "agent_tasks",
            "bulk_operations",
            "apps",
            "artifacts",
            "memories",
            "temp_data",
            "org_members",
        )
        for table_name in org_scoped_tables:
            table_exists_result = await session.execute(
                text("SELECT to_regclass(:table_name)"),
                {"table_name": f"public.{table_name}"},
            )
            if table_exists_result.scalar_one_or_none() is None:
                logger.warning(
                    "Skipping delete for missing org-scoped table table=%s org=%s",
                    table_name,
                    org_uuid,
                )
                continue

            await session.execute(
                text(f"DELETE FROM {table_name} WHERE organization_id = :org_id"),
                {"org_id": org_uuid},
            )

        deleted_guest_users = await session.execute(
            text("DELETE FROM users WHERE organization_id = :org_id AND is_guest IS TRUE"),
            {"org_id": org_uuid},
        )
        logger.info(
            "Deleted guest users for organization org=%s count=%s",
            org_uuid,
            deleted_guest_users.rowcount,
        )

        detached_users = await session.execute(
            text("UPDATE users SET organization_id = NULL WHERE organization_id = :org_id AND is_guest IS NOT TRUE"),
            {"org_id": org_uuid},
        )
        logger.info(
            "Detached non-guest users from organization org=%s count=%s",
            org_uuid,
            detached_users.rowcount,
        )

        await session.delete(org)
        await session.commit()

        logger.warning("Deleted organization org=%s requested_by=%s", org_uuid, auth.user_id)
        return {"status": "deleted"}


@router.patch("/organizations/{org_id}/guest-user")
async def update_guest_user(
    org_id: str,
    request: UpdateGuestUserRequest,
    user_id: Optional[str] = None,
) -> dict[str, bool]:
    """Enable/disable guest user fallback for unmapped Slack identities."""
    try:
        org_uuid = UUID(org_id)
        user_uuid = UUID(user_id) if user_id else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    if not user_uuid:
        raise HTTPException(status_code=401, detail="Not authenticated")

    async with get_session() as session:
        requesting_user = await session.get(User, user_uuid)
        if not await _can_administer_org(session, requesting_user, org_uuid):
            raise HTTPException(status_code=403, detail="Org admin or global_admin required for this organization")

        org = await session.get(Organization, org_uuid)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        if not org.guest_user_id:
            raise HTTPException(status_code=409, detail="Guest user is not configured for this organization")

        guest_user = await session.get(User, org.guest_user_id)
        if not guest_user or guest_user.organization_id != org_uuid or not guest_user.is_guest:
            logger.warning(
                "Refusing to toggle guest user for org=%s because configured guest_user_id=%s is invalid",
                org_uuid,
                org.guest_user_id,
            )
            raise HTTPException(status_code=409, detail="Guest user configuration is invalid")

        org.guest_user_enabled = request.enabled
        await session.commit()
        logger.info("Updated guest user toggle org=%s enabled=%s by_user=%s", org_uuid, request.enabled, user_uuid)
        return {"enabled": bool(org.guest_user_enabled)}


# =============================================================================
# Masquerade (Admin Impersonation) Endpoints
# =============================================================================


class MasqueradeUserResponse(BaseModel):
    """Response model for masquerade user info."""

    id: str
    email: str
    name: Optional[str]
    avatar_url: Optional[str]
    roles: list[str]
    organization: Optional[SyncOrganizationData]


@router.get("/masquerade/{target_user_id}", response_model=MasqueradeUserResponse)
async def get_masquerade_user(
    target_user_id: str,
    admin_user_id: Optional[str] = None,
) -> MasqueradeUserResponse:
    """Get user info for masquerade/impersonation.
    
    Only accessible by global admins. Returns the target user's full profile
    including organization data so the admin can impersonate them.
    """
    if not admin_user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        admin_uuid = UUID(admin_user_id)
        target_uuid = UUID(target_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    async with get_session() as session:
        # Verify admin has global_admin role
        admin_user = await session.get(User, admin_uuid)
        if not admin_user:
            raise HTTPException(status_code=404, detail="Admin user not found")
        
        if "global_admin" not in (admin_user.roles or []):
            raise HTTPException(
                status_code=403, 
                detail="Only global admins can masquerade as other users"
            )
        
        # Fetch target user
        target_user = await session.get(User, target_uuid)
        if not target_user:
            raise HTTPException(status_code=404, detail="Target user not found")
        if getattr(target_user, "is_guest", False):
            raise HTTPException(status_code=403, detail="Guest users cannot be masqueraded as")
        
        # Fetch target user's organization if they have one
        org_data: Optional[SyncOrganizationData] = None
        if target_user.organization_id:
            org = await session.get(Organization, target_user.organization_id)
            if org:
                _sub_ok = (org.subscription_status or "") in ("active", "trialing")
                org_data = SyncOrganizationData(
                    id=str(org.id),
                    name=org.name,
                    logo_url=org.logo_url,
                    handle=org.handle,
                    subscription_required=not _sub_ok,
                )
        
        return MasqueradeUserResponse(
            id=str(target_user.id),
            email=target_user.email,
            name=target_user.name,
            avatar_url=target_user.avatar_url,
            roles=target_user.roles or [],
            organization=org_data,
        )


# =============================================================================
# Nango Integration Endpoints
# =============================================================================


@router.get("/available-integrations", response_model=AvailableIntegrationsResponse)
async def get_available_integrations() -> AvailableIntegrationsResponse:
    """List all available integrations that can be connected."""
    return AvailableIntegrationsResponse(
        integrations=[
            {"id": "hubspot", "name": "HubSpot", "description": "CRM - Deals, Contacts, Companies", "scope": "user"},
            {"id": "slack", "name": "Slack", "description": "Team communication and messages", "scope": "organization"},
            {"id": "google_calendar", "name": "Google Calendar", "description": "Calendar events and meetings", "scope": "user"},
            {"id": "gmail", "name": "Gmail", "description": "Google email communications", "scope": "user"},
            {"id": "microsoft_calendar", "name": "Microsoft Calendar", "description": "Outlook calendar events and meetings", "scope": "user"},
            {"id": "microsoft_mail", "name": "Microsoft Mail", "description": "Outlook emails and communications", "scope": "user"},
            {"id": "salesforce", "name": "Salesforce", "description": "CRM - Opportunities, Accounts", "scope": "user"},
            {"id": "google_drive", "name": "Google Drive", "description": "Sync files from Google Drive — search and read Docs, Sheets, Slides", "scope": "user"},
            {"id": "apollo", "name": "Apollo.io", "description": "Data enrichment - Update contact job titles, companies, emails", "scope": "user"},
            {"id": "github", "name": "GitHub", "description": "Track repos, commits, and pull requests by team", "scope": "user"},
            {"id": "linear", "name": "Linear", "description": "Issue tracking - sync and manage teams, projects, and issues", "scope": "user"},
            {"id": "asana", "name": "Asana", "description": "Project management - sync and manage teams, projects, and tasks", "scope": "user"},
        ]
    )


@router.get("/connect/{provider}", response_model=ConnectUrlResponse)
async def get_connect_url(
    provider: str,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> ConnectUrlResponse:
    """
    Get Nango connect URL for a provider.

    The frontend should redirect the user to this URL to initiate OAuth.
    After OAuth completes, Nango redirects back to our callback.
    Accepts either user_id (to look up org) or organization_id directly.
    """
    org_id_str: str

    if organization_id:
        # Direct organization ID provided
        try:
            UUID(organization_id)  # Validate it's a valid UUID
            org_id_str = organization_id
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid organization ID")
    elif user_id:
        # Look up organization via user
        try:
            user_uuid = UUID(user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user ID")

        async with get_session() as session:
            user = await session.get(User, user_uuid)
            if not user or not user.organization_id:
                raise HTTPException(status_code=404, detail="User not found")
            if user.is_guest:
                raise HTTPException(status_code=403, detail="Guest users cannot connect integrations")
            org_id_str = str(user.organization_id)
    else:
        raise HTTPException(status_code=400, detail="Either user_id or organization_id required")

    # Get Nango integration ID
    try:
        nango_integration_id = get_nango_integration_id(provider)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    # Build Nango connect URL
    nango = get_nango_client()
    redirect_url = f"{settings.FRONTEND_URL}/?integration={provider}&status=success"

    connect_url = await nango.get_connect_url(
        integration_id=nango_integration_id,
        connection_id=org_id_str,
        redirect_url=redirect_url,
    )

    return ConnectUrlResponse(connect_url=connect_url, provider=provider)


@router.get("/connect/{provider}/session", response_model=ConnectSessionResponse)
async def get_connect_session(
    provider: str,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> ConnectSessionResponse:
    """
    Get a Nango connect session token for the frontend SDK.

    This is the recommended approach - returns a session token that
    the frontend uses with @nangohq/frontend to open a popup OAuth flow.

    user_id is REQUIRED (used for connection ID and ownership tracking).
    Connection ID format: "{org_id}:user:{user_id}"
    """
    org_id_str: str = ""
    user_id_str: str | None = None

    if organization_id:
        try:
            UUID(organization_id)
            org_id_str = organization_id
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid organization ID")

    if user_id:
        try:
            user_uuid = UUID(user_id)
            user_id_str = user_id
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user ID")

        async with get_session() as db_session:
            user = await db_session.get(User, user_uuid)
            if not user or not user.organization_id:
                raise HTTPException(status_code=404, detail="User not found")
            if user.is_guest:
                raise HTTPException(status_code=403, detail="Guest users cannot connect integrations")
            org_id_str = str(user.organization_id)

    if not org_id_str:
        raise HTTPException(status_code=400, detail="Either user_id or organization_id required")

    if not user_id_str:
        raise HTTPException(
            status_code=400,
            detail="user_id is required for all integrations"
        )

    try:
        nango_integration_id = get_nango_integration_id(provider)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    # All connections are user-scoped
    connection_id = f"{org_id_str}:user:{user_id_str}"

    org_name: str | None = None
    async with get_session() as db_session:
        from models.organization import Organization
        org_row = await db_session.get(Organization, UUID(org_id_str))
        if org_row:
            org_name = org_row.name

    nango = get_nango_client()
    try:
        session_data = await nango.create_connect_session(
            integration_id=nango_integration_id,
            connection_id=connection_id,
            organization_name=org_name or "Basebase",
        )
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return ConnectSessionResponse(
        session_token=session_data["token"],
        provider=provider,
        expires_at=session_data.get("expires_at"),
        connection_id=connection_id,
    )


class ConfirmConnectionRequest(BaseModel):
    """Request model for confirming a connection after OAuth."""
    provider: str
    connection_id: str
    organization_id: str
    user_id: str  # Required - all integrations are user-scoped


@router.post("/integrations/confirm")
async def confirm_integration(
    request: ConfirmConnectionRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """
    Confirm and create an integration record after successful OAuth.

    Called by the frontend after receiving a success event from Nango.
    Creates integration with pending_sharing_config=true - sync won't start
    until user configures sharing preferences via /integrations/{id}/sharing.
    """
    try:
        org_uuid = UUID(request.organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    try:
        user_uuid = UUID(request.user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    async with get_admin_session() as guard_session:
        requesting_user = await guard_session.get(User, user_uuid)
        if not requesting_user or requesting_user.organization_id != org_uuid:
            raise HTTPException(status_code=403, detail="Not authorized for this organization")
        if requesting_user.is_guest:
            raise HTTPException(status_code=403, detail="Guest users cannot connect integrations")

    # The frontend now passes the actual Nango connection_id from the event callback
    nango_connection_id: str = request.connection_id
    print(f"[Confirm] Received connection_id from frontend: {nango_connection_id}")

    connection_metadata: dict[str, Any] | None = None
    try:
        nango = get_nango_client()
        nango_integration_id = get_nango_integration_id(request.provider)
        connection = await nango.get_connection(nango_integration_id, nango_connection_id)
        if request.provider == "slack":
            _log_slack_nango_connection(connection, nango_connection_id)
        connection_metadata = extract_connection_metadata(connection)
        if request.provider == "slack":
            connection_data = connection.get("data") or {}
            slack_user_payload = connection_data.get("user")
            logger.info(
                "[Confirm] Nango Slack response.data user payload for connection_id=%s: %s",
                nango_connection_id,
                slack_user_payload,
            )
        # For Slack, ensure team_id is present in connection_metadata.
        if request.provider == "slack":
            if connection_metadata is None:
                connection_metadata = {}
            if "team_id" not in connection_metadata:
                creds_raw: dict[str, Any] = (connection.get("credentials") or {}).get("raw") or {}
                raw_team: dict[str, Any] = creds_raw.get("team") or {}
                raw_team_id: str | None = raw_team.get("id") or creds_raw.get("team_id")
                if raw_team_id:
                    connection_metadata["team_id"] = raw_team_id
                    logger.info(
                        "[Confirm] Extracted Slack team_id=%s from credentials.raw for connection_id=%s",
                        raw_team_id,
                        nango_connection_id,
                    )
                else:
                    logger.warning(
                        "[Confirm] Could not extract Slack team_id from Nango connection for connection_id=%s",
                        nango_connection_id,
                    )
        # Prevent an org from connecting two different Slack workspaces.
        if request.provider == "slack":
            _incoming_team_id: str | None = (connection_metadata or {}).get("team_id")
            if _incoming_team_id:
                async with get_session(organization_id=str(org_uuid)) as _check_session:
                    _existing_slack_rows = await _check_session.execute(
                        select(Integration.extra_data).where(
                            Integration.organization_id == org_uuid,
                            Integration.connector == "slack",
                            Integration.is_active.is_(True),
                        )
                    )
                    for (_extra_data_row,) in _existing_slack_rows:
                        _existing_team: str | None = (_extra_data_row or {}).get("team_id")
                        if _existing_team and _existing_team != _incoming_team_id:
                            raise HTTPException(
                                status_code=409,
                                detail=(
                                    "This organization is already connected to a different Slack workspace. "
                                    "All team members must connect to the same workspace."
                                ),
                            )

        # For Slack, extract the bot token from Nango and upsert into
        # messenger_bot_installs so event-handling paths can look it up by team_id.
        # With bot scopes configured in Nango, the top-level access_token is
        # the xoxb- bot token, and completing OAuth installs the app.
        if request.provider == "slack":
            _slack_team_id: str | None = (connection_metadata or {}).get("team_id")
            _slack_credentials: dict[str, Any] = connection.get("credentials") or {}
            _slack_access_token: str | None = _slack_credentials.get("access_token")
            if _slack_team_id and _slack_access_token:
                from services.slack_bot_install import upsert_bot_install as _upsert_bot_install

                try:
                    await _upsert_bot_install(
                        organization_id=org_uuid,
                        team_id=_slack_team_id,
                        access_token=_slack_access_token,
                    )
                    logger.info(
                        "[Confirm] Upserted slack_bot_install for team_id=%s org=%s via Nango confirm",
                        _slack_team_id,
                        org_uuid,
                    )
                except Exception as bot_exc:
                    logger.warning(
                        "[Confirm] Failed to upsert slack_bot_install for team_id=%s org=%s: %s",
                        _slack_team_id,
                        org_uuid,
                        bot_exc,
                    )
            else:
                logger.warning(
                    "[Confirm] Cannot upsert slack_bot_install: team_id=%s has_token=%s connection_id=%s",
                    _slack_team_id,
                    bool(_slack_access_token),
                    nango_connection_id,
                )

        if connection_metadata:
            print(
                f"[Confirm] Retrieved Nango metadata for provider={request.provider}, "
                f"connection_id={nango_connection_id}"
            )
        else:
            print(
                f"[Confirm] No Nango metadata found for provider={request.provider}, "
                f"connection_id={nango_connection_id} keys={sorted(connection.keys())}"
            )
    except Exception as exc:
        print(
            f"[Confirm] Failed to fetch Nango metadata for provider={request.provider}, "
            f"connection_id={nango_connection_id}: {exc}"
        )

    # Get default sharing settings for this provider
    sharing_defaults = get_provider_sharing_defaults(request.provider)

    integration_id: str = ""
    async with get_session(organization_id=str(org_uuid)) as session:
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": str(org_uuid)}
        )

        # Check for existing integration for this user
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == org_uuid,
                Integration.connector == request.provider,
                Integration.user_id == user_uuid,
            )
        )

        existing = result.scalar_one_or_none()

        if existing:
            existing.nango_connection_id = nango_connection_id
            existing.is_active = True
            existing.last_error = None
            existing.updated_at = datetime.utcnow()
            existing.pending_sharing_config = False
            existing.share_synced_data = sharing_defaults.share_synced_data
            existing.share_query_access = sharing_defaults.share_query_access
            existing.share_write_access = sharing_defaults.share_write_access
            if connection_metadata:
                existing.extra_data = connection_metadata
            integration_id = str(existing.id)
        else:
            new_integration = Integration(
                organization_id=org_uuid,
                connector=request.provider,
                user_id=user_uuid,
                scope="user",  # Satisfy DB NOT NULL; column deprecated, all integrations are user-scoped
                nango_connection_id=nango_connection_id,
                connected_by_user_id=user_uuid,
                is_active=True,
                extra_data=connection_metadata,
                share_synced_data=sharing_defaults.share_synced_data,
                share_query_access=sharing_defaults.share_query_access,
                share_write_access=sharing_defaults.share_write_access,
                pending_sharing_config=False,
            )
            session.add(new_integration)
            await session.flush()
            integration_id = str(new_integration.id)

        await session.commit()

    # Trigger initial sync in background (we use defaults, no sharing modal)
    user_id_str = str(user_uuid) if user_uuid else ""
    if user_id_str:
        background_tasks.add_task(
            run_initial_sync,
            str(org_uuid),
            request.provider,
            user_id_str,
        )

    if request.provider == "slack" and user_uuid:
        background_tasks.add_task(
            upsert_slack_user_mappings_from_metadata,
            str(org_uuid),
            user_uuid,
            connection_metadata,
        )

    return {
        "status": "connected",
        "provider": request.provider,
        "integration_id": integration_id,
        "sharing_defaults": {
            "share_synced_data": sharing_defaults.share_synced_data,
            "share_query_access": sharing_defaults.share_query_access,
            "share_write_access": sharing_defaults.share_write_access,
        },
    }


class UpdateSharingRequest(BaseModel):
    """Request to update sharing preferences for an integration."""
    share_synced_data: bool
    share_query_access: bool
    share_write_access: bool


@router.post("/integrations/{integration_id}/sharing")
async def update_integration_sharing(
    integration_id: str,
    request: UpdateSharingRequest,
    background_tasks: BackgroundTasks,
    user_id: str | None = None,
) -> dict[str, Any]:
    """
    Update sharing preferences for an integration.

    Called after OAuth to configure sharing, or later to modify settings.
    If pending_sharing_config was true, clears it and triggers initial sync.
    Only the integration owner can modify sharing settings.
    """
    try:
        integration_uuid = UUID(integration_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid integration ID")

    user_uuid: UUID | None = None
    if user_id:
        try:
            user_uuid = UUID(user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user ID")

    # Use admin session so RLS doesn't hide the row (we only have integration_id, not org_id).
    # Authorization is enforced below by checking integration.user_id.
    async with get_admin_session() as session:
        result = await session.execute(
            select(Integration).where(Integration.id == integration_uuid)
        )
        integration = result.scalar_one_or_none()

        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")

        # Only the owner can modify sharing settings
        if user_uuid and integration.user_id != user_uuid:
            raise HTTPException(
                status_code=403,
                detail="Only the integration owner can modify sharing settings"
            )

        was_pending = integration.pending_sharing_config

        integration.share_synced_data = request.share_synced_data
        integration.share_query_access = request.share_query_access
        integration.share_write_access = request.share_write_access
        integration.pending_sharing_config = False
        integration.updated_at = datetime.utcnow()

        await session.commit()

        org_id_str = str(integration.organization_id)
        user_id_str = str(integration.user_id) if integration.user_id else ""
        provider = integration.connector

    # Propagate share_synced_data to activity visibility
    new_visibility: str = "team" if request.share_synced_data else "owner_only"
    async with get_admin_session() as prop_session:
        await prop_session.execute(
            text(
                "UPDATE activities SET visibility = :vis WHERE integration_id = :iid"
            ),
            {"vis": new_visibility, "iid": integration_uuid},
        )
        await prop_session.commit()

    # If this was the initial sharing config, trigger sync now
    if was_pending:
        background_tasks.add_task(run_initial_sync, org_id_str, provider, user_id_str)

    return {
        "status": "updated",
        "integration_id": integration_id,
        "share_synced_data": request.share_synced_data,
        "share_query_access": request.share_query_access,
        "share_write_access": request.share_write_access,
        "sync_triggered": was_pending,
    }


@router.patch("/integrations/{integration_id}/sharing")
async def patch_integration_sharing(
    integration_id: str,
    request: UpdateSharingRequest,
    user_id: str | None = None,
) -> dict[str, Any]:
    """
    Update sharing preferences for an existing integration (no sync trigger).

    Use POST for initial setup (triggers sync), PATCH for later modifications.
    """
    try:
        integration_uuid = UUID(integration_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid integration ID")

    user_uuid: UUID | None = None
    if user_id:
        try:
            user_uuid = UUID(user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user ID")

    # Use admin session so RLS doesn't hide the row (we only have integration_id).
    # Authorization is enforced by checking integration.user_id.
    async with get_admin_session() as session:
        result = await session.execute(
            select(Integration).where(Integration.id == integration_uuid)
        )
        integration = result.scalar_one_or_none()

        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")

        if user_uuid and integration.user_id != user_uuid:
            raise HTTPException(
                status_code=403,
                detail="Only the integration owner can modify sharing settings"
            )

        integration.share_synced_data = request.share_synced_data
        integration.share_query_access = request.share_query_access
        integration.share_write_access = request.share_write_access
        integration.updated_at = datetime.utcnow()

        await session.commit()

    # Propagate share_synced_data to activity visibility
    new_visibility: str = "team" if request.share_synced_data else "owner_only"
    async with get_admin_session() as prop_session:
        await prop_session.execute(
            text(
                "UPDATE activities SET visibility = :vis WHERE integration_id = :iid"
            ),
            {"vis": new_visibility, "iid": integration_uuid},
        )
        await prop_session.commit()

    return {
        "status": "updated",
        "integration_id": integration_id,
        "share_synced_data": request.share_synced_data,
        "share_query_access": request.share_query_access,
        "share_write_access": request.share_write_access,
    }


def _is_builtin_connector(provider: str) -> bool:
    """Check if a provider is a builtin connector, including dynamic mcp_* slugs."""
    return provider in BUILTIN_CONNECTORS or provider.startswith("mcp_")


class ConnectBuiltinRequest(BaseModel):
    """Request to connect a built-in connector (no OAuth)."""

    organization_id: str
    provider: str  # web_search | code_sandbox | twilio | mcp
    user_id: str  # Required - all integrations are user-scoped
    extra_data: dict[str, Any] | None = None  # Provider-specific config (e.g. MCP endpoint URL)


@router.post("/integrations/connect-builtin")
async def connect_builtin(request: ConnectBuiltinRequest) -> dict[str, Any]:
    """
    Create an Integration row for a built-in connector (Web Search, Code Sandbox, Twilio).

    These connectors use platform credentials and do not go through Nango.
    The user must explicitly "connect" them in the Connectors tab before the agent can use them.
    """
    if not _is_builtin_connector(request.provider):
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{request.provider}' is not a built-in connector. Use the regular connect flow for OAuth integrations.",
        )
    try:
        org_uuid = UUID(request.organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    try:
        user_uuid = UUID(request.user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    # iSpot.tv: store client_id and client_secret in extra_data (client_credentials flow)
    connection_extra_data: dict[str, Any] | None = None
    mcp_tool_count: int = 0
    is_mcp: bool = request.provider == "mcp" or request.provider.startswith("mcp_")
    if request.provider == "ispot_tv":
        if not request.extra_data:
            raise HTTPException(status_code=400, detail="iSpot.tv requires client_id and client_secret in extra_data")
        cid: str | None = (request.extra_data.get("client_id") or "").strip() or None
        csec: str | None = (request.extra_data.get("client_secret") or "").strip() or None
        if not cid or not csec:
            raise HTTPException(status_code=400, detail="iSpot.tv requires non-empty client_id and client_secret")
        connection_extra_data = {"client_id": cid, "client_secret": csec}
    elif is_mcp:
        if not request.extra_data or not request.extra_data.get("endpoint_url"):
            raise HTTPException(status_code=400, detail="MCP connector requires 'endpoint_url' in extra_data")
        endpoint_url: str = request.extra_data["endpoint_url"].strip()
        auth_header: str | None = (request.extra_data.get("auth_header") or request.extra_data.get("bearer_token") or "").strip() or None

        from connectors.mcp import GenericMcpClient
        mcp_client: GenericMcpClient = GenericMcpClient(endpoint_url=endpoint_url, auth_header=auth_header)
        try:
            await mcp_client.initialize()
            discovered_tools: list[dict[str, Any]] = await mcp_client.list_tools()
        except Exception as exc:
            logger.warning("MCP connect validation failed for %s: %s", endpoint_url, exc)
            raise HTTPException(
                status_code=400,
                detail=f"Could not connect to MCP server at {endpoint_url}: {exc}",
            ) from exc

        mcp_tool_count = len(discovered_tools)
        display_name: str = (request.extra_data.get("display_name") or "").strip() or "MCP Server"

        # Generate a stable slug from the display name: "SimilarWeb" → "mcp_similarweb"
        import re as _re
        slug_suffix: str = _re.sub(r"[^a-z0-9]+", "_", display_name.lower()).strip("_")
        if not slug_suffix:
            slug_suffix = "server"
        request.provider = f"mcp_{slug_suffix}"

        connection_extra_data = {
            "display_name": display_name,
            "endpoint_url": endpoint_url,
            "auth_header": auth_header,
            "tools": discovered_tools,
        }

    sharing_defaults = get_provider_sharing_defaults(request.provider)

    try:
        async with get_session(organization_id=request.organization_id) as session:
            await session.execute(
                text("SELECT set_config('app.current_org_id', :org_id, true)"),
                {"org_id": request.organization_id},
            )
            result = await session.execute(
                select(Integration).where(
                    Integration.organization_id == org_uuid,
                    Integration.connector == request.provider,
                    Integration.user_id == user_uuid,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.is_active = True
                existing.nango_connection_id = "builtin"
                existing.updated_at = datetime.utcnow()
                if connection_extra_data:
                    existing.extra_data = connection_extra_data
            else:
                new_integration = Integration(
                    organization_id=org_uuid,
                    connector=request.provider,
                    user_id=user_uuid,
                    scope="user",  # Satisfy DB NOT NULL; column deprecated, all integrations are user-scoped
                    nango_connection_id="builtin",
                    connected_by_user_id=user_uuid,
                    is_active=True,
                    extra_data=connection_extra_data,
                    share_synced_data=sharing_defaults.share_synced_data,
                    share_query_access=sharing_defaults.share_query_access,
                    share_write_access=sharing_defaults.share_write_access,
                    pending_sharing_config=False,  # Built-in connectors don't need sharing config
                )
                session.add(new_integration)
            await session.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "connect_builtin failed: provider=%s org=%s user=%s",
            request.provider,
            request.organization_id,
            request.user_id,
        )
        raise HTTPException(status_code=500, detail="Failed to connect built-in integration.") from e

    response: dict[str, Any] = {"status": "connected", "provider": request.provider}
    if is_mcp:
        response["tools_discovered"] = mcp_tool_count
    return response


@router.get("/connect/{provider}/redirect")
async def connect_redirect(
    provider: str,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> RedirectResponse:
    """
    Redirect to Nango connect URL.

    Alternative to get_connect_url for direct browser redirects.
    Accepts either user_id or organization_id.
    """
    response = await get_connect_url(provider, user_id=user_id, organization_id=organization_id)
    return RedirectResponse(url=response.connect_url)


@router.get("/oauth/callback")
async def nango_oauth_callback_redirect(request: Request) -> RedirectResponse:
    """
    Redirect to Nango's OAuth callback URL.

    This route can be used as the callback URL for any Nango integration.
    It preserves all query parameters and redirects to Nango's callback endpoint.
    """
    nango_callback_url = "https://api.nango.dev/oauth/callback"
    
    # Preserve all query parameters from the incoming request
    query_string = request.url.query
    if query_string:
        redirect_url = f"{nango_callback_url}?{query_string}"
    else:
        redirect_url = nango_callback_url
    
    return RedirectResponse(url=redirect_url, status_code=302)


# --- Slack OAuth callback (Nango Connect flow) ---

def _slack_oauth_callback_url() -> str:
    """Absolute URL for Slack OAuth redirect_uri (must match Slack app config)."""
    base = settings.BACKEND_PUBLIC_URL or ""
    if not base:
        base = "http://localhost:8000"
    return base.rstrip("/") + "/api/auth/slack/oauth-callback"


@router.get("/slack/oauth-callback")
async def slack_oauth_callback(request: Request) -> RedirectResponse:
    """
    Slack OAuth callback — forwards to Nango.

    Configure this URL as the Redirect URL in your Slack app and in Nango's
    Slack integration callback. Nango handles the code exchange and token
    storage; the bot token is then extracted and stored in messenger_bot_installs
    by confirm_integration.
    """
    nango_callback_url = "https://api.nango.dev/oauth/callback"
    query_string: str = request.url.query
    redirect_url: str = f"{nango_callback_url}?{query_string}" if query_string else nango_callback_url
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/callback")
async def nango_callback(
    provider: str,
    connection_id: str,
    user_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """
    Handle callback after Nango OAuth completes (legacy endpoint).

    Prefer using POST /integrations/confirm instead.
    This creates an integration with pending_sharing_config=true.
    """
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    # Extract org_id from connection_id (format: "{org_id}:user:{user_id}")
    if ":user:" in connection_id:
        org_id_str = connection_id.split(":user:")[0]
    else:
        org_id_str = connection_id

    try:
        org_uuid = UUID(org_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID in connection_id")

    nango = get_nango_client()
    nango_integration_id = get_nango_integration_id(provider)

    try:
        connection = await nango.get_connection(nango_integration_id, connection_id)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to verify Nango connection: {str(e)}",
        )

    sharing_defaults = get_provider_sharing_defaults(provider)

    integration_id: str = ""
    async with get_session() as session:
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": str(org_uuid)}
        )

        user = await session.get(User, user_uuid)
        if not user or user.organization_id != org_uuid:
            raise HTTPException(status_code=403, detail="User not authorized")

        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == org_uuid,
                Integration.connector == provider,
                Integration.user_id == user_uuid,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.is_active = True
            existing.last_error = None
            existing.nango_connection_id = connection_id
            existing.updated_at = datetime.utcnow()
            existing.pending_sharing_config = True
            integration_id = str(existing.id)
        else:
            new_integration = Integration(
                organization_id=org_uuid,
                connector=provider,
                user_id=user_uuid,
                scope="user",  # Satisfy DB NOT NULL; column deprecated, all integrations are user-scoped
                nango_connection_id=connection_id,
                connected_by_user_id=user_uuid,
                is_active=True,
                extra_data=connection.get("metadata"),
                share_synced_data=sharing_defaults.share_synced_data,
                share_query_access=sharing_defaults.share_query_access,
                share_write_access=sharing_defaults.share_write_access,
                pending_sharing_config=True,
            )
            session.add(new_integration)
            await session.flush()
            integration_id = str(new_integration.id)

        await session.commit()

    return {
        "status": "pending_sharing_config",
        "provider": provider,
        "integration_id": integration_id,
        "sharing_defaults": {
            "share_synced_data": sharing_defaults.share_synced_data,
            "share_query_access": sharing_defaults.share_query_access,
            "share_write_access": sharing_defaults.share_write_access,
        },
    }


@router.get("/integrations", response_model=IntegrationsListResponse)
async def list_integrations(
    user_id: Optional[str] = None, organization_id: Optional[str] = None
) -> IntegrationsListResponse:
    """List all integrations for a user's organization.

    Returns integrations grouped by provider, showing the current user's
    integration (if any), team connections, and connector scope.
    """
    org_uuid: UUID | None = None
    current_user_uuid: UUID | None = None

    if organization_id:
        try:
            org_uuid = UUID(organization_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid organization ID")

    if user_id:
        try:
            current_user_uuid = UUID(user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user ID")

        async with get_admin_session() as db_session:
            user = await db_session.get(User, current_user_uuid)
            if not user or not user.organization_id:
                raise HTTPException(status_code=404, detail="User not found")
            org_uuid = user.organization_id

    if not org_uuid:
        raise HTTPException(status_code=400, detail="Either user_id or organization_id required")

    from connectors.registry import ConnectorScope

    scope_by_provider: dict[str, str] = _get_scope_by_provider()

    async with get_session(organization_id=str(org_uuid)) as db_session:
        result = await db_session.execute(
            select(Integration).where(Integration.organization_id == org_uuid)
        )
        all_integrations = list(result.scalars().all())

        # Group integrations by provider
        integrations_by_provider: dict[str, list[Integration]] = {}
        for i in all_integrations:
            if i.connector not in integrations_by_provider:
                integrations_by_provider[i.connector] = []
            integrations_by_provider[i.connector].append(i)

        # Get team members
        team_result = await db_session.execute(
            select(User).where(User.organization_id == org_uuid)
        )
        team_members: dict[UUID, User] = {u.id: u for u in team_result.scalars().all()}
        team_total = len(team_members)

        response_integrations: list[IntegrationResponse] = []

        # Include all known providers (from PROVIDER_SHARING_DEFAULTS)
        all_providers = set(integrations_by_provider.keys()) | set(PROVIDER_SHARING_DEFAULTS.keys())

        for provider in sorted(all_providers):
            integrations_for_provider = integrations_by_provider.get(provider, [])

            # Find current user's integration
            current_user_integration = next(
                (i for i in integrations_for_provider if i.user_id == current_user_uuid),
                None
            )

            # Build team connections list (excluding current user)
            team_connections: list[TeamConnection] = []
            for integration in integrations_for_provider:
                if integration.user_id and integration.user_id in team_members:
                    user_obj = team_members[integration.user_id]
                    team_connections.append(TeamConnection(
                        user_id=str(integration.user_id),
                        user_name=user_obj.name or user_obj.email,
                    ))

            # Use current user's integration for display, or the one with most recent sync
            # (sync may update a different integration when multiple exist)
            if current_user_integration:
                ref_integration = current_user_integration
            elif integrations_for_provider:
                ref_integration = max(
                    integrations_for_provider,
                    key=lambda i: (i.last_sync_at or datetime.min),
                )
            else:
                ref_integration = None

            # Get owner name
            connected_by_name: str | None = None
            if ref_integration and ref_integration.user_id in team_members:
                owner = team_members[ref_integration.user_id]
                connected_by_name = owner.name or owner.email

            mcp_display_name: str | None = None
            if provider.startswith("mcp_") and ref_integration and ref_integration.extra_data:
                mcp_display_name = ref_integration.extra_data.get("display_name")

            response_integrations.append(IntegrationResponse(
                id=str(ref_integration.id) if ref_integration else f"pending-{provider}",
                provider=provider,
                is_active=ref_integration.is_active if ref_integration else False,
                last_sync_at=(
                    f"{ref_integration.last_sync_at.isoformat()}Z"
                    if ref_integration and ref_integration.last_sync_at else None
                ),
                last_error=ref_integration.last_error if ref_integration else None,
                connected_at=(
                    f"{ref_integration.created_at.isoformat()}Z"
                    if ref_integration and ref_integration.created_at else None
                ),
                scope=scope_by_provider.get(provider, ConnectorScope.USER.value),
                user_id=str(ref_integration.user_id) if ref_integration else None,
                connected_by=connected_by_name,
                share_synced_data=ref_integration.share_synced_data if ref_integration else False,
                share_query_access=ref_integration.share_query_access if ref_integration else False,
                share_write_access=ref_integration.share_write_access if ref_integration else False,
                pending_sharing_config=ref_integration.pending_sharing_config if ref_integration else False,
                is_owner=(
                    current_user_uuid is not None
                    and ref_integration is not None
                    and ref_integration.user_id == current_user_uuid
                ),
                current_user_connected=current_user_integration is not None,
                team_connections=team_connections,
                team_total=team_total,
                sync_stats=ref_integration.sync_stats if ref_integration else None,
                display_name=mcp_display_name,
            ))

        return IntegrationsListResponse(integrations=response_integrations)


@router.delete("/integrations/{provider}")
async def disconnect_integration(
    provider: str,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    delete_data: bool = False,
) -> dict[str, Any]:
    """Disconnect an integration.
    
    For org-scoped integrations: disconnects the shared org connection.
    For user-scoped integrations: disconnects only the current user's connection.
    
    Args:
        provider: The integration provider to disconnect
        user_id: User ID (required for user-scoped integrations)
        organization_id: Organization ID
        delete_data: If True, also deletes all synced data (activities, contacts, accounts, deals, pipelines, orphaned meetings)
    """
    org_uuid: Optional[UUID] = None
    current_user_uuid: Optional[UUID] = None
    if organization_id:
        try:
            org_uuid = UUID(organization_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid organization ID")

    if user_id:
        try:
            current_user_uuid = UUID(user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user ID")

        async with get_session() as db_session:
            user = await db_session.get(User, current_user_uuid)
            if not user or not user.organization_id:
                raise HTTPException(status_code=404, detail="User not found")
            org_uuid = user.organization_id

    if not org_uuid:
        raise HTTPException(status_code=400, detail="Either user_id or organization_id required")

    if not current_user_uuid:
        raise HTTPException(
            status_code=400,
            detail="user_id is required to disconnect an integration"
        )

    async with get_session() as db_session:
        await db_session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": str(org_uuid)}
        )

        # Find integration for this user
        integration: Integration | None = None

        result = await db_session.execute(
            select(Integration).where(
                Integration.organization_id == org_uuid,
                Integration.connector == provider,
                Integration.user_id == current_user_uuid,
            )
        )
        integration = result.scalar_one_or_none()

        # Fallback: check by nango_connection_id for old records
        if not integration:
            expected_conn_id = f"{org_uuid}:user:{current_user_uuid}"
            result = await db_session.execute(
                select(Integration).where(
                    Integration.organization_id == org_uuid,
                    Integration.connector == provider,
                    Integration.nango_connection_id == expected_conn_id,
                )
            )
            integration = result.scalar_one_or_none()

        # Fallback for org-scoped integrations: any org member can disconnect.
        # Collect ALL matching rows so duplicate integrations are cleaned up in one pass.
        extra_integrations: list[Integration] = []
        if not integration:
            scope_by_provider = _get_scope_by_provider()
            if scope_by_provider.get(provider) == "organization":
                result = await db_session.execute(
                    select(Integration).where(
                        Integration.organization_id == org_uuid,
                        Integration.connector == provider,
                    )
                )
                all_org_integrations: list[Integration] = list(result.scalars().all())
                if all_org_integrations:
                    integration = all_org_integrations[0]
                    extra_integrations = all_org_integrations[1:]

        if not integration:
            print(f"Disconnect: Integration not found for org={org_uuid}, provider={provider}, user={current_user_uuid}")
            raise HTTPException(status_code=404, detail="Integration not found")

        all_to_delete: list[Integration] = [integration] + extra_integrations
        print(f"Disconnect: Found {len(all_to_delete)} integration(s) to delete: {[str(i.id) for i in all_to_delete]}")

        # Delete from Nango for every integration
        nango = get_nango_client()
        nango_integration_id = get_nango_integration_id(provider)
        for integ in all_to_delete:
            if integ.nango_connection_id:
                try:
                    print(f"Disconnect: Deleting from Nango: integration={nango_integration_id}, conn_id={integ.nango_connection_id}")
                    await nango.delete_connection(
                        nango_integration_id,
                        integ.nango_connection_id,
                    )
                    print("Disconnect: Nango deletion successful")
                except Exception as e:
                    print(f"Disconnect: Failed to delete Nango connection {integ.nango_connection_id}: {e}")
            else:
                print(f"Disconnect: No nango_connection_id for {integ.id}, skipping Nango deletion")

        # Always delete rows that reference this integration so we can delete the integration row.
        # 1) Tracker tables (Linear/Jira/Asana): tracker_teams.integration_id -> integrations.id
        source_system_disconnect: str = provider
        if provider == "google-calendar":
            source_system_disconnect = "google_calendar"
        elif provider == "google-mail":
            source_system_disconnect = "gmail"
        params_tracker: dict[str, str] = {
            "org_id": str(org_uuid),
            "source_system": source_system_disconnect,
        }
        await db_session.execute(
            text("""
                DELETE FROM tracker_issues
                WHERE organization_id = :org_id AND source_system = :source_system
            """),
            params_tracker,
        )
        await db_session.execute(
            text("""
                DELETE FROM tracker_projects
                WHERE organization_id = :org_id AND source_system = :source_system
            """),
            params_tracker,
        )
        await db_session.execute(
            text("""
                DELETE FROM tracker_teams
                WHERE organization_id = :org_id AND source_system = :source_system
            """),
            params_tracker,
        )
        # 2) GitHub: github_repositories.integration_id -> integrations.id
        all_integration_ids: list[str] = [str(i.id) for i in all_to_delete]
        for iid in all_integration_ids:
            integration_id_param: dict[str, Any] = {"integration_id": iid}
            await db_session.execute(
                text("""
                    DELETE FROM github_commits
                    WHERE repository_id IN (
                        SELECT id FROM github_repositories WHERE integration_id = :integration_id
                    )
                """),
                integration_id_param,
            )
            await db_session.execute(
                text("""
                    DELETE FROM github_pull_requests
                    WHERE repository_id IN (
                        SELECT id FROM github_repositories WHERE integration_id = :integration_id
                    )
                """),
                integration_id_param,
            )
            await db_session.execute(
                text("DELETE FROM github_repositories WHERE integration_id = :integration_id"),
                integration_id_param,
            )

        # Optionally delete all synced data from this provider
        deleted_activities: int = 0
        deleted_contacts: int = 0
        deleted_accounts: int = 0
        deleted_deals: int = 0
        deleted_goals: int = 0
        deleted_pipelines: int = 0
        deleted_meetings: int = 0
        
        if delete_data:
            print(f"Disconnect: Deleting all data from source_system={provider}")
            
            # Map provider names to source_system values
            # Some providers have different names in the activities table
            source_system: str = provider
            if provider == "google-calendar":
                source_system = "google_calendar"
            elif provider == "google-mail":
                source_system = "gmail"
            
            params: dict[str, str] = {"org_id": str(org_uuid), "source_system": source_system}
            
            # 1. Delete all activities from this source_system
            result = await db_session.execute(
                text("""
                    DELETE FROM activities 
                    WHERE organization_id = :org_id 
                      AND source_system = :source_system
                    RETURNING id
                """),
                params,
            )
            deleted_activities = len(result.fetchall())
            print(f"Disconnect: Deleted {deleted_activities} activities")
            
            # 2. Null out FK references in remaining activities that point to
            #    CRM objects we're about to delete (e.g. a Google Calendar activity
            #    linked to a HubSpot contact). This avoids FK constraint violations.
            for fk_col, table in [
                ("deal_id", "deals"),
                ("contact_id", "contacts"),
                ("account_id", "accounts"),
            ]:
                await db_session.execute(
                    text(f"""
                        UPDATE activities
                        SET {fk_col} = NULL
                        WHERE organization_id = :org_id
                          AND {fk_col} IN (
                              SELECT id FROM {table}
                              WHERE organization_id = :org_id
                                AND source_system = :source_system
                          )
                    """),
                    params,
                )
            
            # 3. Delete deals (references accounts and pipelines via FK)
            result = await db_session.execute(
                text("""
                    DELETE FROM deals
                    WHERE organization_id = :org_id
                      AND source_system = :source_system
                    RETURNING id
                """),
                params,
            )
            deleted_deals = len(result.fetchall())
            print(f"Disconnect: Deleted {deleted_deals} deals")
            
            # 4. Delete contacts (references accounts via FK)
            result = await db_session.execute(
                text("""
                    DELETE FROM contacts
                    WHERE organization_id = :org_id
                      AND source_system = :source_system
                    RETURNING id
                """),
                params,
            )
            deleted_contacts = len(result.fetchall())
            print(f"Disconnect: Deleted {deleted_contacts} contacts")
            
            # 5. Delete accounts (companies)
            result = await db_session.execute(
                text("""
                    DELETE FROM accounts
                    WHERE organization_id = :org_id
                      AND source_system = :source_system
                    RETURNING id
                """),
                params,
            )
            deleted_accounts = len(result.fetchall())
            print(f"Disconnect: Deleted {deleted_accounts} accounts")
            
            # 6. Delete goals
            result = await db_session.execute(
                text("""
                    DELETE FROM goals
                    WHERE organization_id = :org_id
                      AND source_system = :source_system
                    RETURNING id
                """),
                params,
            )
            deleted_goals = len(result.fetchall())
            print(f"Disconnect: Deleted {deleted_goals} goals")
            
            # 7. Delete pipelines (stages cascade via ON DELETE CASCADE)
            result = await db_session.execute(
                text("""
                    DELETE FROM pipelines
                    WHERE organization_id = :org_id
                      AND source_system = :source_system
                    RETURNING id
                """),
                params,
            )
            deleted_pipelines = len(result.fetchall())
            print(f"Disconnect: Deleted {deleted_pipelines} pipelines")
            
            # 8. Clean up orphaned meetings (meetings with no linked activities).
            # This is best-effort because activity ingestion can happen concurrently
            # and create a fresh FK reference between orphan selection and deletion.
            # Use a savepoint so a rare FK violation won't abort the entire disconnect.
            try:
                async with db_session.begin_nested():
                    result = await db_session.execute(
                        text("""
                            DELETE FROM meetings m
                            WHERE m.organization_id = :org_id
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM activities a
                                  WHERE a.meeting_id = m.id
                              )
                            RETURNING id
                        """),
                        {"org_id": str(org_uuid)},
                    )
                    deleted_meetings = len(result.fetchall())
                print(f"Disconnect: Deleted {deleted_meetings} orphaned meetings")
            except IntegrityError as exc:
                logger.warning(
                    "Disconnect: Skipped orphaned meeting cleanup due to FK race org_id=%s provider=%s error=%s",
                    org_uuid,
                    provider,
                    exc,
                )

        print(f"Disconnect: Deleting {len(all_to_delete)} integration(s) from database")
        for integ in all_to_delete:
            await db_session.delete(integ)
        await db_session.commit()
        print(f"Disconnect: Database deletion successful")

    response: dict[str, Any] = {"status": "disconnected", "provider": provider}
    if delete_data:
        response["deleted_activities"] = deleted_activities
        response["deleted_contacts"] = deleted_contacts
        response["deleted_accounts"] = deleted_accounts
        response["deleted_deals"] = deleted_deals
        response["deleted_goals"] = deleted_goals
        response["deleted_pipelines"] = deleted_pipelines
        response["deleted_meetings"] = deleted_meetings
    return response


# =============================================================================
# User Merge (consolidate duplicate accounts)
# =============================================================================


class MergeUsersRequest(BaseModel):
    """Request model for merging two user accounts."""

    target_user_id: str = Field(..., description="The user ID to keep (receives all records)")
    source_user_id: str = Field(..., description="The user ID to merge away (will be deleted)")
    delete_source: bool = Field(True, description="Whether to delete the source user after merge")


class MergeUsersResponse(BaseModel):
    """Response model for user merge operation."""

    success: bool
    target_user_id: str
    source_user_id: str
    source_email: str
    tables_updated: dict[str, int]
    error: Optional[str] = None


@router.post("/users/merge", response_model=MergeUsersResponse)
async def merge_users_endpoint(
    request: MergeUsersRequest,
    auth: AuthContext = Depends(get_current_auth),
) -> MergeUsersResponse:
    """
    Merge two user accounts into one.
    
    This is useful when a user has multiple accounts (e.g., different email
    addresses for Slack vs web app login) and needs to consolidate them.
    
    The source user's records are either:
    - Reassigned to the target user (ownership, authorship)
    - Deleted (conversations, messages, user mappings)
    
    Requires admin role or global_admin.
    """
    from services.user_merge import merge_users
    
    if not auth.organization_id:
        raise HTTPException(status_code=400, detail="Organization context required")
    
    # Check admin permissions (org admin for current org, or global_admin).
    async with get_admin_session() as session:
        requester: Optional[User] = await session.get(User, auth.user_id)
        if not await _can_administer_org(session, requester, auth.organization_id):
            raise HTTPException(
                status_code=403,
                detail="Org admin for this organization or global_admin role required",
            )
    
    result = await merge_users(
        target_user_id=request.target_user_id,
        source_user_id=request.source_user_id,
        organization_id=auth.organization_id,
        delete_source=request.delete_source,
    )
    
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    
    return MergeUsersResponse(
        success=result.success,
        target_user_id=result.target_user_id,
        source_user_id=result.source_user_id,
        source_email=result.source_email,
        tables_updated=result.tables_updated,
        error=result.error,
    )


# =============================================================================
# Simple User Registration (for MVP without full OAuth signup)
# =============================================================================


class CreateUserRequest(BaseModel):
    """Request model for creating a user."""

    email: str
    name: Optional[str] = None
    company_name: Optional[str] = None


class CreateUserResponse(BaseModel):
    """Response model for created user."""

    user_id: str
    organization_id: str


@router.post("/register", response_model=CreateUserResponse)
async def register_user(request: CreateUserRequest) -> CreateUserResponse:
    """
    Simple user registration for MVP.

    Creates a user and organization record without OAuth.
    """
    async with get_session() as session:
        # Check if user exists
        result = await session.execute(
            select(User).where(User.email == request.email)
        )
        existing_user = result.scalar_one_or_none()

        if existing_user:
            return CreateUserResponse(
                user_id=str(existing_user.id),
                organization_id=str(existing_user.organization_id) if existing_user.organization_id else "",
            )

        from models.org_member import OrgMember

        # Create customer
        organization = Organization(
            name=request.company_name or f"{request.email}'s Company",
        )
        session.add(organization)
        await session.flush()

        # Create user
        user = User(
            email=request.email,
            name=request.name,
            organization_id=organization.id,
            role="member",
        )
        session.add(user)
        await session.flush()

        # Create membership
        membership = OrgMember(
            user_id=user.id,
            organization_id=organization.id,
            role="admin",
            status="active",
            joined_at=datetime.utcnow(),
        )
        session.add(membership)
        await session.commit()

        return CreateUserResponse(
            user_id=str(user.id),
            organization_id=str(organization.id),
        )


# =============================================================================
# Background Sync Helper
# =============================================================================


async def run_initial_sync(
    organization_id: str,
    provider: str,
    user_id: Optional[str] = None,
) -> None:
    """
    Run initial data sync after OAuth connection.
    
    This runs in the background so the user isn't blocked waiting.
    
    Args:
        organization_id: UUID of the organization
        provider: Provider name (e.g., "gmail", "hubspot")
        user_id: UUID of the user (required for user-scoped integrations)
    """
    from connectors.hubspot import HubSpotConnector
    from connectors.salesforce import SalesforceConnector
    from connectors.slack import SlackConnector
    from connectors.google_calendar import GoogleCalendarConnector
    from connectors.gmail import GmailConnector
    from connectors.microsoft_calendar import MicrosoftCalendarConnector
    from connectors.microsoft_mail import MicrosoftMailConnector
    from connectors.fireflies import FirefliesConnector
    from connectors.zoom import ZoomConnector
    from connectors.github import GitHubConnector
    from connectors.linear import LinearConnector
    from connectors.asana import AsanaConnector
    from connectors.granola import GranolaConnector

    # Google Drive uses a different sync pattern (not BaseConnector)
    if provider == "google_drive":
        await _run_initial_drive_sync(organization_id, user_id)
        return

    connectors = {
        "hubspot": HubSpotConnector,
        "salesforce": SalesforceConnector,
        "slack": SlackConnector,
        "google_calendar": GoogleCalendarConnector,
        "gmail": GmailConnector,
        "microsoft_calendar": MicrosoftCalendarConnector,
        "microsoft_mail": MicrosoftMailConnector,
        "fireflies": FirefliesConnector,
        "zoom": ZoomConnector,
        "github": GitHubConnector,
        "linear": LinearConnector,
        "asana": AsanaConnector,
        "granola": GranolaConnector,
    }

    connector_class = connectors.get(provider)
    if not connector_class:
        print(f"No connector for provider: {provider}")
        return

    try:
        print(f"Starting initial sync for {provider} (org: {organization_id}, user: {user_id})")
        connector = connector_class(organization_id, user_id=user_id)
        counts = await connector.sync_all()
        await connector.update_last_sync(counts)
        print(f"Initial sync complete for {provider}: {counts}")
    except Exception as e:
        print(f"Initial sync failed for {provider}: {str(e)}")
        # Record the error
        try:
            connector = connector_class(organization_id, user_id=user_id)
            await connector.record_error(str(e))
        except Exception:
            pass  # Ignore errors while recording error


async def _run_initial_drive_sync(organization_id: str, user_id: Optional[str]) -> None:
    """Run initial Google Drive metadata sync after OAuth."""
    if not user_id:
        print("[GoogleDrive] Skipping initial sync: user_id required")
        return

    try:
        from connectors.google_drive import GoogleDriveConnector

        print(f"Starting initial Google Drive sync (org: {organization_id}, user: {user_id})")
        connector = GoogleDriveConnector(organization_id, user_id)
        counts: dict[str, int] = await connector.sync_file_metadata()
        total: int = sum(counts.values())

        # Update integration sync stats
        async with get_session(organization_id=organization_id) as session:
            result = await session.execute(
                select(Integration).where(
                    Integration.organization_id == UUID(organization_id),
                    Integration.connector == "google_drive",
                    Integration.user_id == UUID(user_id),
                )
            )
            integration: Optional[Integration] = result.scalar_one_or_none()
            if integration:
                integration.last_sync_at = datetime.utcnow()
                integration.sync_stats = {"total_files": total, **counts}
                await session.commit()

        print(f"Initial Google Drive sync complete: {total} files ({counts})")
    except Exception as e:
        print(f"Initial Google Drive sync failed: {str(e)}")

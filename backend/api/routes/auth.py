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

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select, text

from config import settings, get_nango_integration_id, get_provider_scope
from models.database import get_admin_session, get_session
from models.integration import Integration
from models.user import User
from models.organization import Organization
from services.nango import extract_connection_metadata, get_nango_client
from services.slack_conversations import upsert_slack_user_mappings_from_metadata

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


class UserResponse(BaseModel):
    """Response model for user info."""

    id: str
    email: str
    name: Optional[str]
    role: Optional[str]
    avatar_url: Optional[str]
    organization_id: Optional[str]


class TeamConnection(BaseModel):
    """A team member who has connected a user-scoped integration."""
    
    user_id: str
    user_name: str


class IntegrationResponse(BaseModel):
    """Response model for integration status."""

    id: str
    provider: str
    scope: str  # 'organization' or 'user'
    is_active: bool
    last_sync_at: Optional[str]
    last_error: Optional[str]
    connected_at: Optional[str]
    # For org-scoped integrations
    connected_by: Optional[str] = None
    # For user-scoped integrations
    current_user_connected: bool = False
    team_connections: list[TeamConnection] = []
    team_total: int = 0
    # Sync statistics - counts of objects synced
    sync_stats: Optional[dict[str, int]] = None


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

    async with get_admin_session() as session:
        user = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        return UserResponse(
            id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
            avatar_url=user.avatar_url,
            organization_id=str(user.organization_id) if user.organization_id else None,
        )


class UpdateProfileRequest(BaseModel):
    """Request model for updating user profile."""

    name: Optional[str] = None
    avatar_url: Optional[str] = None


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

    async with get_admin_session() as session:
        user = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Update fields if provided
        if request.name is not None:
            user.name = request.name
        if request.avatar_url is not None:
            user.avatar_url = request.avatar_url

        await session.commit()
        await session.refresh(user)

        return UserResponse(
            id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
            avatar_url=user.avatar_url,
            organization_id=str(user.organization_id) if user.organization_id else None,
        )


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    """Clear session."""
    return {"status": "logged out"}


class CreateOrganizationRequest(BaseModel):
    """Request model for creating an organization."""

    id: str  # UUID from frontend
    name: str
    email_domain: str


class OrganizationResponse(BaseModel):
    """Response model for organization."""

    id: str
    name: str
    email_domain: Optional[str]
    logo_url: Optional[str] = None


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


class SyncUserResponse(BaseModel):
    """Response model for synced user."""

    id: str
    email: str
    name: Optional[str]
    avatar_url: Optional[str]
    organization_id: Optional[str]
    organization: Optional[SyncOrganizationData] = None
    status: str  # 'waitlist', 'invited', 'active'
    roles: list[str]  # Global roles like ['global_admin']


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
            # Always update last_login on sync (user just logged in)
            existing.last_login = datetime.utcnow()
            
            # Update organization if provided and different
            if org_uuid and existing.organization_id != org_uuid:
                existing.organization_id = org_uuid
            
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
            
            await session.commit()
            await session.refresh(existing)
            
            # Load organization data if user has one
            org_data: Optional[SyncOrganizationData] = None
            if existing.organization_id:
                org = await session.get(Organization, existing.organization_id)
                if org:
                    org_data = SyncOrganizationData(
                        id=str(org.id),
                        name=org.name,
                        logo_url=org.logo_url,
                    )
            
            return SyncUserResponse(
                id=str(existing.id),
                email=existing.email,
                name=existing.name,
                avatar_url=existing.avatar_url,
                organization_id=str(existing.organization_id) if existing.organization_id else None,
                organization=org_data,
                status=existing.status,
                roles=existing.roles or [],
            )

        # User doesn't exist - check if their email domain has an approved org
        email_domain = request.email.split("@")[1].lower() if "@" in request.email else None
        
        if email_domain:
            # Check if an organization exists for this domain (means someone from their company is already approved)
            result = await session.execute(
                select(Organization).where(Organization.email_domain == email_domain)
            )
            existing_org = result.scalar_one_or_none()
            
            if existing_org:
                # Auto-create user as active - they're a colleague of an approved user
                new_user = User(
                    id=user_uuid,
                    email=request.email,
                    name=request.name,
                    avatar_url=request.avatar_url,
                    organization_id=existing_org.id,
                    status="active",
                    role="member",
                    last_login=datetime.utcnow(),
                )
                session.add(new_user)
                await session.commit()
                await session.refresh(new_user)
                
                # Include organization data for new user
                org_data = SyncOrganizationData(
                    id=str(existing_org.id),
                    name=existing_org.name,
                    logo_url=existing_org.logo_url,
                )
                
                return SyncUserResponse(
                    id=str(new_user.id),
                    email=new_user.email,
                    name=new_user.name,
                    avatar_url=new_user.avatar_url,
                    organization_id=str(new_user.organization_id),
                    organization=org_data,
                    status=new_user.status,
                    roles=new_user.roles or [],
                )
        
        # No approved org for their domain - they need to join the waitlist
        raise HTTPException(
            status_code=403,
            detail="Please join the waitlist first. Visit the homepage to sign up.",
        )


@router.get("/organizations/by-domain/{email_domain}", response_model=OrganizationResponse)
async def get_organization_by_domain(email_domain: str) -> OrganizationResponse:
    """Get organization by email domain.
    
    Used to check if an organization exists for a domain when a new user signs up.
    """
    async with get_admin_session() as session:
        result = await session.execute(
            select(Organization).where(Organization.email_domain == email_domain)
        )
        org = result.scalar_one_or_none()
        
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        
        return OrganizationResponse(
            id=str(org.id),
            name=org.name,
            email_domain=org.email_domain,
            logo_url=org.logo_url,
        )


@router.post("/organizations", response_model=OrganizationResponse)
async def create_organization(request: CreateOrganizationRequest) -> OrganizationResponse:
    """Create a new organization.
    
    Called when the first user from a company domain signs up.
    """
    try:
        org_uuid = UUID(request.id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    async with get_session() as session:
        # Check if organization already exists by ID
        existing = await session.get(Organization, org_uuid)
        if existing:
            return OrganizationResponse(
                id=str(existing.id),
                name=existing.name,
                email_domain=existing.email_domain,
                logo_url=existing.logo_url,
            )
        
        # Check if organization exists for this email domain (different browser scenario)
        result = await session.execute(
            select(Organization).where(Organization.email_domain == request.email_domain)
        )
        existing_by_domain = result.scalar_one_or_none()
        if existing_by_domain:
            return OrganizationResponse(
                id=str(existing_by_domain.id),
                name=existing_by_domain.name,
                email_domain=existing_by_domain.email_domain,
                logo_url=existing_by_domain.logo_url,
            )

        # Create new organization
        new_org = Organization(
            id=org_uuid,
            name=request.name,
            email_domain=request.email_domain,
        )
        session.add(new_org)
        await session.commit()
        await session.refresh(new_org)

        return OrganizationResponse(
            id=str(new_org.id),
            name=new_org.name,
            email_domain=new_org.email_domain,
            logo_url=new_org.logo_url,
        )


class TeamMemberResponse(BaseModel):
    """Response model for a team member."""

    id: str
    name: Optional[str]
    email: str
    role: Optional[str]
    avatar_url: Optional[str]


class TeamMembersListResponse(BaseModel):
    """Response model for list of team members."""

    members: list[TeamMemberResponse]


@router.get("/organizations/{org_id}/members", response_model=TeamMembersListResponse)
async def get_organization_members(
    org_id: str,
    user_id: Optional[str] = None,
) -> TeamMembersListResponse:
    """Get all team members for an organization.
    
    Only accessible by members of that organization.
    """
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        org_uuid = UUID(org_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session() as session:
        # Verify requesting user belongs to this organization
        requesting_user = await session.get(User, user_uuid)
        if not requesting_user or requesting_user.organization_id != org_uuid:
            raise HTTPException(status_code=403, detail="Not authorized to view this organization's members")

        # Fetch all users in the organization
        result = await session.execute(
            select(User).where(User.organization_id == org_uuid)
        )
        users = result.scalars().all()

        return TeamMembersListResponse(
            members=[
                TeamMemberResponse(
                    id=str(u.id),
                    name=u.name,
                    email=u.email,
                    role=u.role,
                    avatar_url=u.avatar_url,
                )
                for u in users
            ]
        )


class UpdateOrganizationRequest(BaseModel):
    """Request model for updating organization settings."""

    name: Optional[str] = None
    logo_url: Optional[str] = None


@router.patch("/organizations/{org_id}", response_model=OrganizationResponse)
async def update_organization(
    org_id: str,
    request: UpdateOrganizationRequest,
    user_id: Optional[str] = None,
) -> OrganizationResponse:
    """Update organization settings.
    
    Only accessible by admin members of that organization.
    """
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        org_uuid = UUID(org_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session() as session:
        # Verify requesting user belongs to this organization
        requesting_user = await session.get(User, user_uuid)
        if not requesting_user or requesting_user.organization_id != org_uuid:
            raise HTTPException(status_code=403, detail="Not authorized to update this organization")

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
        )


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
        
        # Fetch target user's organization if they have one
        org_data: Optional[SyncOrganizationData] = None
        if target_user.organization_id:
            org = await session.get(Organization, target_user.organization_id)
            if org:
                org_data = SyncOrganizationData(
                    id=str(org.id),
                    name=org.name,
                    logo_url=org.logo_url,
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
            {"id": "hubspot", "name": "HubSpot", "description": "CRM - Deals, Contacts, Companies"},
            {"id": "slack", "name": "Slack", "description": "Team communication and messages"},
            {"id": "google_calendar", "name": "Google Calendar", "description": "Calendar events and meetings"},
            {"id": "gmail", "name": "Gmail", "description": "Google email communications"},
            {"id": "microsoft_calendar", "name": "Microsoft Calendar", "description": "Outlook calendar events and meetings"},
            {"id": "microsoft_mail", "name": "Microsoft Mail", "description": "Outlook emails and communications"},
            {"id": "salesforce", "name": "Salesforce", "description": "CRM - Opportunities, Accounts"},
            {"id": "google_sheets", "name": "Google Sheets", "description": "Import contacts, accounts, deals from spreadsheets"},
            {"id": "apollo", "name": "Apollo.io", "description": "Data enrichment - Update contact job titles, companies, emails"},
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

    For user-scoped integrations (email, calendar), user_id is REQUIRED.
    For org-scoped integrations (CRMs), either user_id or organization_id works.
    
    Connection ID format:
    - Organization-scoped: "{org_id}"
    - User-scoped: "{org_id}:user:{user_id}"
    """
    org_id_str: str = ""
    user_id_str: Optional[str] = None
    
    # Get the scope for this provider
    scope = get_provider_scope(provider)

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
            org_id_str = str(user.organization_id)
    
    if not org_id_str:
        raise HTTPException(status_code=400, detail="Either user_id or organization_id required")
    
    # For user-scoped integrations, user_id is required
    if scope == "user" and not user_id_str:
        raise HTTPException(
            status_code=400, 
            detail=f"{provider} is a user-scoped integration. user_id is required."
        )

    try:
        nango_integration_id = get_nango_integration_id(provider)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    # Build connection ID based on scope
    if scope == "user":
        connection_id = f"{org_id_str}:user:{user_id_str}"
    else:
        connection_id = org_id_str

    nango = get_nango_client()
    try:
        session_data = await nango.create_connect_session(
            integration_id=nango_integration_id,
            connection_id=connection_id,
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
    user_id: Optional[str] = None


@router.post("/integrations/confirm")
async def confirm_integration(
    request: ConfirmConnectionRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """
    Confirm and create an integration record after successful OAuth.
    
    Called by the frontend after receiving a success event from Nango.
    Queries Nango to get the actual connection_id and stores it.
    """
    try:
        org_uuid = UUID(request.organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")
    
    user_uuid: Optional[UUID] = None
    if request.user_id:
        try:
            user_uuid = UUID(request.user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user ID")
    
    scope = get_provider_scope(request.provider)
    
    # For user-scoped integrations, user_id is required
    if scope == "user" and not user_uuid:
        raise HTTPException(
            status_code=400,
            detail=f"{request.provider} is a user-scoped integration. user_id is required."
        )
    
    # The frontend now passes the actual Nango connection_id from the event callback
    # We trust this value and store it directly
    nango_connection_id: str = request.connection_id
    print(f"[Confirm] Received connection_id from frontend: {nango_connection_id}")

    connection_metadata: Optional[dict[str, Any]] = None
    try:
        nango = get_nango_client()
        nango_integration_id = get_nango_integration_id(request.provider)
        connection = await nango.get_connection(nango_integration_id, nango_connection_id)
        connection_metadata = extract_connection_metadata(connection)
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
    
    async with get_session() as session:
        # Set RLS context
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": str(org_uuid)}
        )
        
        # Check for existing integration
        if scope == "user":
            result = await session.execute(
                select(Integration).where(
                    Integration.organization_id == org_uuid,
                    Integration.provider == request.provider,
                    Integration.user_id == user_uuid,
                )
            )
        else:
            result = await session.execute(
                select(Integration).where(
                    Integration.organization_id == org_uuid,
                    Integration.provider == request.provider,
                    Integration.user_id.is_(None),
                )
            )
        
        existing = result.scalar_one_or_none()
        
        if existing:
            # Update existing integration with the actual Nango connection_id
            existing.nango_connection_id = nango_connection_id
            existing.is_active = True
            existing.last_error = None
            existing.updated_at = datetime.utcnow()
            if connection_metadata:
                existing.extra_data = connection_metadata
        else:
            # Create new integration with the actual Nango connection_id
            new_integration = Integration(
                organization_id=org_uuid,
                provider=request.provider,
                scope=scope,
                user_id=user_uuid if scope == "user" else None,
                nango_connection_id=nango_connection_id,
                connected_by_user_id=user_uuid,
                is_active=True,
                extra_data=connection_metadata,
            )
            session.add(new_integration)
        
        await session.commit()
    
    # Trigger initial sync in the background
    # For user-scoped integrations, pass user_id so the connector can find the right integration
    user_id_for_sync: Optional[str] = str(user_uuid) if scope == "user" and user_uuid else None
    background_tasks.add_task(run_initial_sync, str(org_uuid), request.provider, user_id_for_sync)

    if request.provider == "slack" and user_uuid:
        background_tasks.add_task(
            upsert_slack_user_mappings_from_metadata,
            str(org_uuid),
            user_uuid,
            connection_metadata,
        )
    
    return {"status": "confirmed", "provider": request.provider}


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


@router.post("/callback")
async def nango_callback(
    provider: str,
    connection_id: str,
    user_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """
    Handle callback after Nango OAuth completes.

    This is called by the frontend after Nango redirects back.
    We record the integration in our database and trigger initial sync.
    """
    try:
        user_uuid = UUID(user_id)
        org_uuid = UUID(connection_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    # Verify the connection exists in Nango
    nango = get_nango_client()
    nango_integration_id = get_nango_integration_id(provider)

    try:
        connection = await nango.get_connection(nango_integration_id, connection_id)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to verify Nango connection: {str(e)}",
        )

    # Record integration in our database
    async with get_session() as session:
        # Set RLS context so queries/inserts work for this organization
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": str(org_uuid)}
        )
        
        # Verify user
        user = await session.get(User, user_uuid)
        if not user or user.organization_id != org_uuid:
            raise HTTPException(status_code=403, detail="User not authorized")

        # Check for existing integration
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == org_uuid,
                Integration.provider == provider,
            )
        )
        integration = result.scalar_one_or_none()

        if integration:
            integration.is_active = True
            integration.last_error = None
            integration.nango_connection_id = connection_id
            integration.updated_at = datetime.utcnow()
        else:
            integration = Integration(
                organization_id=org_uuid,
                provider=provider,
                nango_connection_id=connection_id,
                connected_by_user_id=user_uuid,
                is_active=True,
                extra_data=connection.get("metadata"),
            )
            session.add(integration)

        await session.commit()

    # Trigger initial sync in the background
    # For user-scoped integrations, pass user_id so the connector can find the right integration
    scope = get_provider_scope(provider)
    user_id_for_sync: Optional[str] = str(user_uuid) if scope == "user" else None
    background_tasks.add_task(run_initial_sync, str(org_uuid), provider, user_id_for_sync)

    return {"status": "connected", "provider": provider, "sync_started": True}


@router.get("/integrations", response_model=IntegrationsListResponse)
async def list_integrations(
    user_id: Optional[str] = None, organization_id: Optional[str] = None
) -> IntegrationsListResponse:
    """List all integrations for a user's organization.
    
    Returns both org-scoped and user-scoped integrations.
    For user-scoped integrations, includes team connection status.
    
    Checks both our local database AND Nango for connections,
    syncing any new connections found in Nango to our database.
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

    # We no longer query Nango to filter integrations
    # Just show what's in the database - the nango_connection_id is stored there
    # This avoids issues with inconsistent end_user.id values in Nango

    async with get_session(organization_id=str(org_uuid)) as db_session:
        # RLS context is set by get_session()
        
        # Get all integrations from our database for this org
        result = await db_session.execute(
            select(Integration).where(Integration.organization_id == org_uuid)
        )
        all_integrations = list(result.scalars().all())
        
        # Use PROVIDER_SCOPES as the canonical source of truth for scope,
        # not the stored scope field (which may be stale/incorrect for older records).
        from config import PROVIDER_SCOPES

        # Build lookup structures
        # For org-scoped: key is provider
        # For user-scoped: key is (provider, user_id)
        org_scoped_integrations: dict[str, Integration] = {}
        user_scoped_integrations: dict[str, list[Integration]] = {}
        
        for i in all_integrations:
            canonical_scope: str = PROVIDER_SCOPES.get(i.provider, "organization")
            if canonical_scope == "user":
                if i.provider not in user_scoped_integrations:
                    user_scoped_integrations[i.provider] = []
                user_scoped_integrations[i.provider].append(i)
            else:
                org_scoped_integrations[i.provider] = i

        # Get team members for validating user IDs and building response
        team_result = await db_session.execute(
            select(User).where(User.organization_id == org_uuid)
        )
        team_members: dict[UUID, User] = {u.id: u for u in team_result.scalars().all()}
        team_total = len(team_members)

        # Integration records are created by confirm_integration endpoint after OAuth
        # We trust the database records - no filtering by Nango
        # The stored nango_connection_id will be used when fetching tokens

        # Build response
        response_integrations: list[IntegrationResponse] = []
        
        # Add org-scoped integrations
        for provider, integration in org_scoped_integrations.items():
            # Note: don't call refresh() - the data is already loaded and refresh
            # can fail due to RLS/session state changes after commits
            
            # Get connected_by user name
            connected_by_name: Optional[str] = None
            if integration.connected_by_user_id:
                connected_by_user = team_members.get(integration.connected_by_user_id)
                if connected_by_user:
                    connected_by_name = connected_by_user.name or connected_by_user.email
            
            response_integrations.append(IntegrationResponse(
                id=str(integration.id),
                provider=integration.provider,
                scope="organization",
                is_active=integration.is_active,
                last_sync_at=f"{integration.last_sync_at.isoformat()}Z" if integration.last_sync_at else None,
                last_error=integration.last_error,
                connected_at=f"{integration.created_at.isoformat()}Z" if integration.created_at else None,
                connected_by=connected_by_name,
                current_user_connected=True,  # Org-scoped is shared, so always "connected" for UI
                team_connections=[],
                team_total=team_total,
                sync_stats=integration.sync_stats,
            ))
        
        # Add user-scoped integrations (aggregated by provider)
        all_user_scoped_providers = set(user_scoped_integrations.keys())
        # Also include providers that are user-scoped but have no connections yet
        for p, s in PROVIDER_SCOPES.items():
            if s == "user":
                all_user_scoped_providers.add(p)
        
        for provider in all_user_scoped_providers:
            integrations_for_provider = user_scoped_integrations.get(provider, [])
            
            # Check if current user has connected
            current_user_integration = next(
                (i for i in integrations_for_provider if i.user_id == current_user_uuid),
                None
            )
            
            # Build team connections list
            team_connections: list[TeamConnection] = []
            for integration in integrations_for_provider:
                if integration.user_id and integration.user_id in team_members:
                    user_obj = team_members[integration.user_id]
                    team_connections.append(TeamConnection(
                        user_id=str(integration.user_id),
                        user_name=user_obj.name or user_obj.email,
                    ))
            
            # Use current user's integration for metadata if available
            ref_integration = current_user_integration or (integrations_for_provider[0] if integrations_for_provider else None)
            
            response_integrations.append(IntegrationResponse(
                id=str(ref_integration.id) if ref_integration else f"pending-{provider}",
                provider=provider,
                scope="user",
                is_active=ref_integration.is_active if ref_integration else False,
                last_sync_at=f"{ref_integration.last_sync_at.isoformat()}Z" if ref_integration and ref_integration.last_sync_at else None,
                last_error=ref_integration.last_error if ref_integration else None,
                connected_at=f"{ref_integration.created_at.isoformat()}Z" if ref_integration and ref_integration.created_at else None,
                connected_by=None,
                current_user_connected=current_user_integration is not None,
                team_connections=team_connections,
                team_total=team_total,
                sync_stats=ref_integration.sync_stats if ref_integration else None,
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
        delete_data: If True, also deletes all synced data (activities, orphaned meetings)
    """
    org_uuid: Optional[UUID] = None
    current_user_uuid: Optional[UUID] = None
    scope = get_provider_scope(provider)

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
    
    # For user-scoped integrations, user_id is required
    if scope == "user" and not current_user_uuid:
        raise HTTPException(
            status_code=400,
            detail=f"{provider} is a user-scoped integration. user_id is required."
        )

    async with get_session() as db_session:
        # Set RLS context so queries/deletes work for this organization
        await db_session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": str(org_uuid)}
        )
        
        # Find integration based on scope
        integration: Optional[Integration] = None
        
        if scope == "user":
            # First try to find by user_id
            result = await db_session.execute(
                select(Integration).where(
                    Integration.organization_id == org_uuid,
                    Integration.provider == provider,
                    Integration.user_id == current_user_uuid,
                )
            )
            integration = result.scalar_one_or_none()
            
            # Fallback: check by nango_connection_id for old records where user_id wasn't set
            if not integration:
                expected_conn_id = f"{org_uuid}:user:{current_user_uuid}"
                result = await db_session.execute(
                    select(Integration).where(
                        Integration.organization_id == org_uuid,
                        Integration.provider == provider,
                        Integration.nango_connection_id == expected_conn_id,
                    )
                )
                integration = result.scalar_one_or_none()
                
                # If found via connection_id, update the user_id field for future queries
                if integration and integration.user_id is None:
                    print(f"Disconnect: Fixing missing user_id on integration {integration.id}")
                    integration.user_id = current_user_uuid
        else:
            # Find org-level integration
            result = await db_session.execute(
                select(Integration).where(
                    Integration.organization_id == org_uuid,
                    Integration.provider == provider,
                    Integration.user_id.is_(None),
                )
            )
            integration = result.scalar_one_or_none()

        if not integration:
            print(f"Disconnect: Integration not found for org={org_uuid}, provider={provider}, user={current_user_uuid}")
            raise HTTPException(status_code=404, detail="Integration not found")

        print(f"Disconnect: Found integration id={integration.id}, nango_conn_id={integration.nango_connection_id}")

        # Delete from Nango
        if integration.nango_connection_id:
            try:
                nango = get_nango_client()
                nango_integration_id = get_nango_integration_id(provider)
                print(f"Disconnect: Deleting from Nango: integration={nango_integration_id}, conn_id={integration.nango_connection_id}")
                await nango.delete_connection(
                    nango_integration_id,
                    integration.nango_connection_id,
                )
                print("Disconnect: Nango deletion successful")
            except Exception as e:
                print(f"Disconnect: Failed to delete Nango connection: {e}")
        else:
            print("Disconnect: No nango_connection_id, skipping Nango deletion")

        # Optionally delete all synced data from this provider
        deleted_activities = 0
        deleted_meetings = 0
        
        if delete_data:
            print(f"Disconnect: Deleting all data from source_system={provider}")
            
            # Map provider names to source_system values
            # Some providers have different names in the activities table
            source_system = provider
            if provider == "google-calendar":
                source_system = "google_calendar"
            elif provider == "google-mail":
                source_system = "gmail"
            
            from models.activity import Activity
            from models.meeting import Meeting
            
            # Delete all activities from this source_system
            result = await db_session.execute(
                text("""
                    DELETE FROM activities 
                    WHERE organization_id = :org_id 
                      AND source_system = :source_system
                    RETURNING id
                """),
                {"org_id": str(org_uuid), "source_system": source_system}
            )
            deleted_activities = len(result.fetchall())
            print(f"Disconnect: Deleted {deleted_activities} activities")
            
            # Clean up orphaned meetings (meetings with no linked activities)
            result = await db_session.execute(
                text("""
                    DELETE FROM meetings
                    WHERE organization_id = :org_id
                      AND id NOT IN (SELECT DISTINCT meeting_id FROM activities WHERE meeting_id IS NOT NULL)
                    RETURNING id
                """),
                {"org_id": str(org_uuid)}
            )
            deleted_meetings = len(result.fetchall())
            print(f"Disconnect: Deleted {deleted_meetings} orphaned meetings")

        print(f"Disconnect: Deleting integration from database")
        await db_session.delete(integration)
        await db_session.commit()
        print(f"Disconnect: Database deletion successful")

    response: dict[str, Any] = {"status": "disconnected", "provider": provider}
    if delete_data:
        response["deleted_activities"] = deleted_activities
        response["deleted_meetings"] = deleted_meetings
    return response


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
            role="admin",
        )
        session.add(user)
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

"""
Authentication routes using Nango for OAuth.

Nango handles all OAuth complexity:
- OAuth flows and consent screens
- Token storage and encryption
- Automatic token refresh

Endpoints:
- GET /api/auth/connect/{provider} - Get Nango connect URL
- POST /api/auth/callback - Handle Nango OAuth callback
- GET /api/auth/integrations - List connected integrations
- DELETE /api/auth/integrations/{provider} - Disconnect integration
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select

from config import settings, get_nango_integration_id
from models.database import get_session
from models.integration import Integration
from models.user import User
from models.organization import Organization
from services.nango import get_nango_client

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
    organization_id: Optional[str]


class IntegrationResponse(BaseModel):
    """Response model for integration status."""

    id: str
    provider: str
    is_active: bool
    last_sync_at: Optional[str]
    last_error: Optional[str]
    connected_at: Optional[str]


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

    async with get_session() as session:
        user = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        return UserResponse(
            id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
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


class SyncUserRequest(BaseModel):
    """Request model for syncing a user from Supabase auth."""

    id: str  # Supabase user ID
    email: str
    name: Optional[str] = None
    organization_id: Optional[str] = None


class SyncUserResponse(BaseModel):
    """Response model for synced user."""

    id: str
    email: str
    name: Optional[str]
    organization_id: Optional[str]


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

    async with get_session() as session:
        # Check if user already exists
        existing = await session.get(User, user_uuid)
        if existing:
            # Update organization if provided and different
            if org_uuid and existing.organization_id != org_uuid:
                existing.organization_id = org_uuid
                await session.commit()
                await session.refresh(existing)
            
            return SyncUserResponse(
                id=str(existing.id),
                email=existing.email,
                name=existing.name,
                organization_id=str(existing.organization_id) if existing.organization_id else None,
            )

        # Create new user
        new_user = User(
            id=user_uuid,
            email=request.email,
            name=request.name,
            organization_id=org_uuid,
        )
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)

        return SyncUserResponse(
            id=str(new_user.id),
            email=new_user.email,
            name=new_user.name,
            organization_id=str(new_user.organization_id) if new_user.organization_id else None,
        )


@router.get("/organizations/by-domain/{email_domain}", response_model=OrganizationResponse)
async def get_organization_by_domain(email_domain: str) -> OrganizationResponse:
    """Get organization by email domain.
    
    Used to check if an organization exists for a domain when a new user signs up.
    """
    async with get_session() as session:
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
            {"id": "microsoft_calendar", "name": "Microsoft Calendar", "description": "Outlook calendar events and meetings"},
            {"id": "microsoft_mail", "name": "Microsoft Mail", "description": "Outlook emails and communications"},
            {"id": "salesforce", "name": "Salesforce", "description": "CRM - Opportunities, Accounts"},
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

    Accepts either user_id (looks up organization) or organization_id directly.
    """
    org_id_str: str = ""

    if organization_id:
        try:
            UUID(organization_id)
            org_id_str = organization_id
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid organization ID")
    elif user_id:
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

    try:
        nango_integration_id = get_nango_integration_id(provider)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    nango = get_nango_client()
    try:
        session_data = await nango.create_connect_session(
            integration_id=nango_integration_id,
            connection_id=org_id_str,
        )
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return ConnectSessionResponse(
        session_token=session_data["token"],
        provider=provider,
        expires_at=session_data.get("expires_at"),
    )


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
    background_tasks.add_task(run_initial_sync, str(org_uuid), provider)

    return {"status": "connected", "provider": provider, "sync_started": True}


@router.get("/integrations", response_model=IntegrationsListResponse)
async def list_integrations(
    user_id: Optional[str] = None, organization_id: Optional[str] = None
) -> IntegrationsListResponse:
    """List all connected integrations for a user's organization.
    
    Checks both our local database AND Nango for connections,
    syncing any new connections found in Nango to our database.
    """
    org_uuid: Optional[UUID] = None

    if organization_id:
        try:
            org_uuid = UUID(organization_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid organization ID")
    elif user_id:
        try:
            user_uuid = UUID(user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user ID")

        async with get_session() as session:
            user = await session.get(User, user_uuid)
            if not user or not user.organization_id:
                raise HTTPException(status_code=404, detail="User not found")
            org_uuid = user.organization_id
    else:
        raise HTTPException(status_code=400, detail="Either user_id or organization_id required")

    # Check Nango for connections and sync to our database
    nango = get_nango_client()
    try:
        nango_connections = await nango.list_connections(end_user_id=str(org_uuid))
        print(f"Found {len(nango_connections)} Nango connections for org {org_uuid}")
    except Exception as e:
        print(f"Failed to fetch Nango connections: {e}")
        nango_connections = []

    async with get_session() as session:
        # Ensure organization exists before inserting integrations
        if nango_connections:
            existing_org = await session.get(Organization, org_uuid)
            if not existing_org:
                # Create the organization
                new_org = Organization(
                    id=org_uuid,
                    name="Organization",  # Will be updated when user sets company name
                )
                session.add(new_org)
                await session.flush()  # Ensure org is created before adding integrations
        
        # Get existing integrations from our database
        result = await session.execute(
            select(Integration).where(Integration.organization_id == org_uuid)
        )
        existing_integrations = {i.provider: i for i in result.scalars().all()}

        # Map Nango provider names to our internal provider names
        # Note: "microsoft" Nango integration is used for both calendar and mail
        nango_to_internal_providers: dict[str, list[str]] = {
            "microsoft": ["microsoft_calendar", "microsoft_mail"],  # Both use same OAuth
            "google-calendar": ["google_calendar"],
        }

        # Sync Nango connections to our database
        for conn in nango_connections:
            nango_provider = conn.get("provider_config_key") or conn.get("provider")
            # Get the actual Nango connection ID
            nango_conn_id = conn.get("connection_id") or conn.get("id")
            
            # Map to internal provider name(s) - some Nango integrations map to multiple internal providers
            internal_providers = nango_to_internal_providers.get(nango_provider, [nango_provider])
            print(f"Nango connection: nango_provider={nango_provider}, internal_providers={internal_providers}, conn_id={nango_conn_id}")
            
            for provider in internal_providers:
                if provider and provider not in existing_integrations:
                    # Create new integration record with actual Nango connection ID
                    new_integration = Integration(
                        organization_id=org_uuid,
                        provider=provider,
                        nango_connection_id=nango_conn_id,
                        is_active=True,
                    )
                    session.add(new_integration)
                    existing_integrations[provider] = new_integration
                elif provider and nango_conn_id:
                    # Update existing integration with correct Nango connection ID if different
                    existing = existing_integrations[provider]
                    if existing.nango_connection_id != nango_conn_id:
                        existing.nango_connection_id = nango_conn_id
        
        await session.commit()
        
        # Refresh to get any auto-generated fields
        for integration in existing_integrations.values():
            await session.refresh(integration)

        return IntegrationsListResponse(
            integrations=[
                IntegrationResponse(
                    id=str(i.id),
                    provider=i.provider,
                    is_active=i.is_active,
                    last_sync_at=i.last_sync_at.isoformat() if i.last_sync_at else None,
                    last_error=i.last_error,
                    connected_at=i.created_at.isoformat() if i.created_at else None,
                )
                for i in existing_integrations.values()
            ]
        )


@router.delete("/integrations/{provider}")
async def disconnect_integration(
    provider: str,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> dict[str, str]:
    """Disconnect an integration."""
    org_uuid: Optional[UUID] = None

    if organization_id:
        try:
            org_uuid = UUID(organization_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid organization ID")
    elif user_id:
        try:
            user_uuid = UUID(user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user ID")

        async with get_session() as session:
            user = await session.get(User, user_uuid)
            if not user or not user.organization_id:
                raise HTTPException(status_code=404, detail="User not found")
            org_uuid = user.organization_id
    else:
        raise HTTPException(status_code=400, detail="Either user_id or organization_id required")

    async with get_session() as session:
        # Find integration
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == org_uuid,
                Integration.provider == provider,
            )
        )
        integration = result.scalar_one_or_none()

        if not integration:
            print(f"Disconnect: Integration not found for org={org_uuid}, provider={provider}")
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
                # Log but don't fail - connection might already be gone
                print(f"Disconnect: Failed to delete Nango connection: {e}")
        else:
            print("Disconnect: No nango_connection_id, skipping Nango deletion")

        # Mark as inactive in our database and DELETE the record
        print(f"Disconnect: Deleting integration from database")
        await session.delete(integration)
        await session.commit()
        print(f"Disconnect: Database deletion successful")

    return {"status": "disconnected", "provider": provider}


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


async def run_initial_sync(organization_id: str, provider: str) -> None:
    """
    Run initial data sync after OAuth connection.
    
    This runs in the background so the user isn't blocked waiting.
    """
    from connectors.hubspot import HubSpotConnector
    from connectors.salesforce import SalesforceConnector
    from connectors.slack import SlackConnector
    from connectors.google_calendar import GoogleCalendarConnector
    from connectors.microsoft_calendar import MicrosoftCalendarConnector
    from connectors.microsoft_mail import MicrosoftMailConnector

    connectors = {
        "hubspot": HubSpotConnector,
        "salesforce": SalesforceConnector,
        "slack": SlackConnector,
        "google_calendar": GoogleCalendarConnector,
        "microsoft_calendar": MicrosoftCalendarConnector,
        "microsoft_mail": MicrosoftMailConnector,
    }

    connector_class = connectors.get(provider)
    if not connector_class:
        print(f"No connector for provider: {provider}")
        return

    try:
        print(f"Starting initial sync for {provider} (org: {organization_id})")
        connector = connector_class(organization_id)
        counts = await connector.sync_all()
        await connector.update_last_sync()
        print(f"Initial sync complete for {provider}: {counts}")
    except Exception as e:
        print(f"Initial sync failed for {provider}: {str(e)}")
        # Record the error
        try:
            connector = connector_class(organization_id)
            await connector.record_error(str(e))
        except Exception:
            pass  # Ignore errors while recording error

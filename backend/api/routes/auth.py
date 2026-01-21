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

from fastapi import APIRouter, HTTPException, Response
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
            {"id": "salesforce", "name": "Salesforce", "description": "CRM - Opportunities, Accounts"},
        ]
    )


@router.get("/connect/{provider}", response_model=ConnectUrlResponse)
async def get_connect_url(provider: str, user_id: str) -> ConnectUrlResponse:
    """
    Get Nango connect URL for a provider.

    The frontend should redirect the user to this URL to initiate OAuth.
    After OAuth completes, Nango redirects back to our callback.
    """
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    # Get user and organization
    async with get_session() as session:
        user = await session.get(User, user_uuid)
        if not user or not user.organization_id:
            raise HTTPException(status_code=404, detail="User not found")

        organization_id = str(user.organization_id)

    # Get Nango integration ID
    try:
        nango_integration_id = get_nango_integration_id(provider)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    # Build Nango connect URL
    nango = get_nango_client()
    redirect_url = f"{settings.FRONTEND_URL}/?integration={provider}&status=success"

    connect_url = nango.get_connect_url(
        integration_id=nango_integration_id,
        connection_id=organization_id,
        redirect_url=redirect_url,
    )

    return ConnectUrlResponse(connect_url=connect_url, provider=provider)


@router.get("/connect/{provider}/redirect")
async def connect_redirect(provider: str, user_id: str) -> RedirectResponse:
    """
    Redirect to Nango connect URL.

    Alternative to get_connect_url for direct browser redirects.
    """
    response = await get_connect_url(provider, user_id)
    return RedirectResponse(url=response.connect_url)


@router.post("/callback")
async def nango_callback(
    provider: str,
    connection_id: str,
    user_id: str,
) -> dict[str, str]:
    """
    Handle callback after Nango OAuth completes.

    This is called by the frontend after Nango redirects back.
    We record the integration in our database.
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

    return {"status": "connected", "provider": provider}


@router.get("/integrations", response_model=IntegrationsListResponse)
async def list_integrations(user_id: str) -> IntegrationsListResponse:
    """List all connected integrations for a user's organization."""
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    async with get_session() as session:
        user = await session.get(User, user_uuid)
        if not user or not user.organization_id:
            raise HTTPException(status_code=404, detail="User not found")

        result = await session.execute(
            select(Integration).where(Integration.organization_id == user.organization_id)
        )
        integrations = result.scalars().all()

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
                for i in integrations
            ]
        )


@router.delete("/integrations/{provider}")
async def disconnect_integration(provider: str, user_id: str) -> dict[str, str]:
    """Disconnect an integration."""
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    async with get_session() as session:
        user = await session.get(User, user_uuid)
        if not user or not user.organization_id:
            raise HTTPException(status_code=404, detail="User not found")

        # Find integration
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == user.organization_id,
                Integration.provider == provider,
            )
        )
        integration = result.scalar_one_or_none()

        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")

        # Delete from Nango
        if integration.nango_connection_id:
            try:
                nango = get_nango_client()
                nango_integration_id = get_nango_integration_id(provider)
                await nango.delete_connection(
                    nango_integration_id,
                    integration.nango_connection_id,
                )
            except Exception as e:
                # Log but don't fail - connection might already be gone
                print(f"Warning: Failed to delete Nango connection: {e}")

        # Mark as inactive in our database
        integration.is_active = False
        integration.nango_connection_id = None
        await session.commit()

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

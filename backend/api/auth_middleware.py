"""
Authentication middleware for JWT verification.

This module provides secure authentication by:
1. Extracting and verifying Supabase JWT tokens from Authorization headers
2. Looking up the user in our database to get their organization_id
3. Returning a verified AuthContext that routes can trust

SECURITY: Never trust user_id or organization_id from client query parameters.
Always use the AuthContext returned by these dependencies.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import httpx
from fastapi import Depends, Header, HTTPException, Query, WebSocket, status
from jose import JWTError, jwt
from jose.constants import ALGORITHMS
from sqlalchemy import select

from config import settings
from models.database import get_session
from models.user import User

logger = logging.getLogger(__name__)

# Cache for JWKS public keys
_jwks_cache: dict | None = None


@dataclass
class AuthContext:
    """
    Verified authentication context.
    
    All values are cryptographically verified from the JWT token
    and looked up from our database. Routes should ONLY use these
    values, never client-provided parameters.
    """
    user_id: UUID
    organization_id: Optional[UUID]
    email: str
    role: str
    is_global_admin: bool
    
    @property
    def user_id_str(self) -> str:
        """String representation of user_id for APIs that need strings."""
        return str(self.user_id)
    
    @property
    def organization_id_str(self) -> Optional[str]:
        """String representation of organization_id for APIs that need strings."""
        return str(self.organization_id) if self.organization_id else None


def _extract_token(authorization: Optional[str]) -> str:
    """
    Extract the JWT token from the Authorization header.
    
    Args:
        authorization: The Authorization header value (e.g., "Bearer <token>")
        
    Returns:
        The extracted token string
        
    Raises:
        HTTPException: If header is missing or malformed
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return parts[1]


async def _get_jwks() -> dict:
    """
    Fetch and cache JWKS (JSON Web Key Set) from Supabase.
    
    Returns:
        The JWKS dictionary containing public keys
    """
    global _jwks_cache
    
    if _jwks_cache is not None:
        return _jwks_cache
    
    # Extract Supabase project URL from VITE config or construct from settings
    # The JWKS endpoint is at /.well-known/jwks.json
    supabase_url = settings.SUPABASE_URL
    if not supabase_url:
        # Try to infer from JWT secret presence
        logger.error("SUPABASE_URL not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication not configured",
        )
    
    jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(jwks_url)
            response.raise_for_status()
            _jwks_cache = response.json()
            logger.info(f"Fetched JWKS from {jwks_url}")
            return _jwks_cache
    except Exception as e:
        logger.error(f"Failed to fetch JWKS from {jwks_url}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch authentication keys",
        )


def _get_signing_key(jwks: dict, token: str) -> dict:
    """
    Find the correct signing key from JWKS based on the token's kid header.
    
    Args:
        jwks: The JWKS dictionary
        token: The JWT token string
        
    Returns:
        The matching key from JWKS
    """
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    kid = unverified_header.get("kid")
    if not kid:
        # Fallback: try legacy HS256 if no kid
        if settings.SUPABASE_JWT_SECRET:
            return {"kty": "oct", "k": settings.SUPABASE_JWT_SECRET}
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing key ID",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    
    # Key not found - clear cache and fail
    global _jwks_cache
    _jwks_cache = None
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token signed with unknown key",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _verify_jwt(token: str) -> dict:
    """
    Verify the JWT token and return the payload.
    
    Supports both:
    - ES256 (ECC P-256) - new Supabase default, uses JWKS
    - HS256 (symmetric) - legacy Supabase, uses shared secret
    
    Args:
        token: The JWT token string
        
    Returns:
        The decoded JWT payload
        
    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        unverified_header = jwt.get_unverified_header(token)
        alg = unverified_header.get("alg", "HS256")
    except JWTError as e:
        logger.warning(f"Failed to decode token header: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    try:
        if alg == "ES256":
            # Modern Supabase: use JWKS public key
            jwks = await _get_jwks()
            signing_key = _get_signing_key(jwks, token)
            
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["ES256"],
                options={
                    "verify_aud": False,  # Supabase sets aud to "authenticated"
                }
            )
        else:
            # Legacy Supabase: use shared secret
            if not settings.SUPABASE_JWT_SECRET:
                logger.error("SUPABASE_JWT_SECRET not configured for HS256 token")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Authentication not configured",
                )
            
            payload = jwt.decode(
                token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                options={
                    "verify_aud": False,
                }
            )
        
        return payload
    except JWTError as e:
        logger.warning(f"JWT verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def _get_user_from_token(payload: dict) -> User:
    """
    Look up the user in our database using the JWT subject claim.
    
    Args:
        payload: The verified JWT payload
        
    Returns:
        The User model instance
        
    Raises:
        HTTPException: If user not found or invalid
    """
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing subject",
        )
    
    try:
        user_uuid = UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: malformed subject",
        )
    
    # Use admin session to bypass RLS for user lookup
    from models.database import get_admin_session
    async with get_admin_session() as session:
        user = await session.get(User, user_uuid)
        
        if not user:
            logger.warning(f"User not found for JWT subject: {sub}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        
        if user.status == "crm_only":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Please sign up to use Revtops",
            )
        
        return user


async def get_current_auth(
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> AuthContext:
    """
    FastAPI dependency that verifies the JWT and returns AuthContext.
    
    This is the PRIMARY authentication mechanism. Use this for all
    protected routes instead of accepting user_id as a query parameter.
    
    Usage:
        @router.get("/protected")
        async def protected_route(auth: AuthContext = Depends(get_current_auth)):
            # auth.user_id and auth.organization_id are verified
            ...
    
    Args:
        authorization: The Authorization header (injected by FastAPI)
        
    Returns:
        AuthContext with verified user and organization info
        
    Raises:
        HTTPException: If authentication fails
    """
    token = _extract_token(authorization)
    payload = await _verify_jwt(token)
    user = await _get_user_from_token(payload)
    
    return AuthContext(
        user_id=user.id,
        organization_id=user.organization_id,
        email=user.email,
        role=user.role or "user",
        is_global_admin=user.role == "global_admin",
    )


async def get_optional_auth(
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> Optional[AuthContext]:
    """
    Optional authentication - returns None if no valid auth provided.
    
    Use this for endpoints that work both authenticated and unauthenticated.
    
    Usage:
        @router.get("/public-or-private")
        async def route(auth: Optional[AuthContext] = Depends(get_optional_auth)):
            if auth:
                # Authenticated user
            else:
                # Anonymous access
    """
    if not authorization:
        return None
    
    try:
        token = _extract_token(authorization)
        payload = await _verify_jwt(token)
        user = await _get_user_from_token(payload)
        return AuthContext(
            user_id=user.id,
            organization_id=user.organization_id,
            email=user.email,
            role=user.role or "user",
            is_global_admin=user.role == "global_admin",
        )
    except HTTPException:
        return None


async def require_organization(
    auth: AuthContext = Depends(get_current_auth),
) -> AuthContext:
    """
    Require that the authenticated user belongs to an organization.
    
    Usage:
        @router.get("/org-only")
        async def route(auth: AuthContext = Depends(require_organization)):
            # auth.organization_id is guaranteed to be non-None
    """
    if not auth.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not associated with an organization",
        )
    return auth


async def require_global_admin(
    auth: AuthContext = Depends(get_current_auth),
) -> AuthContext:
    """
    Require that the authenticated user is a global admin.
    
    Usage:
        @router.get("/admin-only")
        async def route(auth: AuthContext = Depends(require_global_admin)):
            # Only global admins can access
    """
    if not auth.is_global_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Global admin access required",
        )
    return auth


async def verify_websocket_token(websocket: WebSocket) -> AuthContext:
    """
    Verify JWT for WebSocket connections.
    
    WebSockets can pass the token via:
    1. Query parameter: ws://host/path?token=<jwt>
    2. Sec-WebSocket-Protocol header
    
    Usage:
        @app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            auth = await verify_websocket_token(websocket)
            await websocket.accept()
            ...
    
    Args:
        websocket: The WebSocket connection
        
    Returns:
        AuthContext with verified user info
        
    Raises:
        WebSocket close with 4001 code if auth fails
    """
    # Try query parameter first (most common for WebSocket)
    token = websocket.query_params.get("token")
    
    # Fall back to Authorization header
    if not token:
        auth_header = websocket.headers.get("authorization")
        if auth_header:
            try:
                token = _extract_token(auth_header)
            except HTTPException:
                token = None
    
    if not token:
        await websocket.close(code=4001, reason="Missing authentication token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
        )
    
    try:
        payload = await _verify_jwt(token)
        user = await _get_user_from_token(payload)
        
        return AuthContext(
            user_id=user.id,
            organization_id=user.organization_id,
            email=user.email,
            role=user.role or "user",
            is_global_admin=user.role == "global_admin",
        )
    except HTTPException as e:
        await websocket.close(code=4001, reason=str(e.detail))
        raise

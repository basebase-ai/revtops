"""
Authentication middleware for JWT verification.

This module provides secure authentication by:
1. Extracting and verifying Supabase JWT tokens from Authorization headers
2. Looking up the user in our database
3. Resolving active organization from X-Organization-Id (validated via org_members)
   or guest default from users.guest_organization_id
4. Returning a verified AuthContext that routes can trust

SECURITY: Never trust user_id from client query parameters alone.
Organization scope from X-Organization-Id is validated against org_members (or guest org).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import asyncio

import httpx
from fastapi import Depends, Header, HTTPException, Query, WebSocket, status
from jose import JWTError, jwt
from jose.constants import ALGORITHMS
from sqlalchemy import select

from config import settings
from models.org_member import OrgMember
from models.user import User
from services.incident_throttling import evaluate_incident_creation
from services.pagerduty import create_pagerduty_incident

logger = logging.getLogger(__name__)

# Cache for JWKS public keys
_jwks_cache: dict | None = None
_jwks_cache_fetched_at: float | None = None
_jwks_cache_lock = asyncio.Lock()

# Keep JWKS warm in memory and avoid per-request network calls.
_JWKS_CACHE_TTL_SECONDS = 60 * 60

# ---------------------------------------------------------------------------
# In-memory auth cache — avoids 2 admin DB sessions (~6 SQL round trips) per
# request by caching User lookups and org-membership checks.  Each entry
# expires after _AUTH_CACHE_TTL_SECONDS so role/membership changes propagate
# within that window.
# ---------------------------------------------------------------------------
_AUTH_CACHE_TTL_SECONDS: int = 60
_AUTH_CACHE_MAX_ENTRIES: int = 500

# {user_uuid: (User, monotonic_timestamp)}
_user_cache: dict[UUID, tuple[User, float]] = {}
# {(user_uuid, org_uuid): (is_active_member, monotonic_timestamp)}
_org_membership_cache: dict[tuple[UUID, UUID], tuple[bool, float]] = {}


def _cache_get_user(user_id: UUID) -> User | None:
    entry: tuple[User, float] | None = _user_cache.get(user_id)
    if entry is not None and (time.monotonic() - entry[1]) < _AUTH_CACHE_TTL_SECONDS:
        return entry[0]
    _user_cache.pop(user_id, None)
    return None


def _cache_set_user(user_id: UUID, user: User) -> None:
    if len(_user_cache) >= _AUTH_CACHE_MAX_ENTRIES:
        # Evict oldest quarter
        to_evict: list[UUID] = list(_user_cache.keys())[: _AUTH_CACHE_MAX_ENTRIES // 4]
        for k in to_evict:
            del _user_cache[k]
    _user_cache[user_id] = (user, time.monotonic())


def _cache_get_org_membership(user_id: UUID, org_id: UUID) -> bool | None:
    entry: tuple[bool, float] | None = _org_membership_cache.get((user_id, org_id))
    if entry is not None and (time.monotonic() - entry[1]) < _AUTH_CACHE_TTL_SECONDS:
        return entry[0]
    _org_membership_cache.pop((user_id, org_id), None)
    return None


def _cache_set_org_membership(user_id: UUID, org_id: UUID, is_member: bool) -> None:
    if len(_org_membership_cache) >= _AUTH_CACHE_MAX_ENTRIES:
        to_evict: list[tuple[UUID, UUID]] = list(_org_membership_cache.keys())[
            : _AUTH_CACHE_MAX_ENTRIES // 4
        ]
        for k in to_evict:
            del _org_membership_cache[k]
    _org_membership_cache[(user_id, org_id)] = (is_member, time.monotonic())


@dataclass
class AuthContext:
    """
    Verified authentication context.

    user_id and email come from the verified JWT + DB user lookup.
    organization_id is resolved from X-Organization-Id (or WebSocket org_id query)
    when present and validated against org_members; guests default to
    users.guest_organization_id when the header is omitted.
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
    global _jwks_cache, _jwks_cache_fetched_at

    # Fast-path without waiting on the lock.
    if _jwks_cache is not None and _jwks_cache_fetched_at is not None:
        cache_age = time.time() - _jwks_cache_fetched_at
        if cache_age < _JWKS_CACHE_TTL_SECONDS:
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
    
    # Single-flight so we do not stampede external JWKS during traffic spikes.
    async with _jwks_cache_lock:
        # Re-check cache after acquiring the lock.
        if _jwks_cache is not None and _jwks_cache_fetched_at is not None:
            cache_age = time.time() - _jwks_cache_fetched_at
            if cache_age < _JWKS_CACHE_TTL_SECONDS:
                return _jwks_cache

        # Try with retries and timeout
        max_retries = 3
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                    response = await client.get(jwks_url)
                    response.raise_for_status()
                    jwks_payload = response.json()
                    if not isinstance(jwks_payload, dict) or not isinstance(jwks_payload.get("keys"), list):
                        raise ValueError("Invalid JWKS payload: missing keys list")

                    _jwks_cache = jwks_payload
                    _jwks_cache_fetched_at = time.time()
                    logger.info("Fetched JWKS from %s (keys=%d)", jwks_url, len(jwks_payload.get("keys", [])))
                    return _jwks_cache
            except Exception as e:
                last_error = e
                logger.warning("JWKS fetch attempt %d/%d failed: %s", attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))  # Backoff

        if _jwks_cache is not None:
            # Stale-if-error fallback: continue serving existing keys during transient outages.
            cache_age = (
                (time.time() - _jwks_cache_fetched_at)
                if _jwks_cache_fetched_at is not None
                else -1
            )
            logger.warning(
                "Using stale JWKS cache after fetch failure (age_seconds=%.2f): %s",
                cache_age,
                last_error,
            )
            return _jwks_cache

    logger.error("Failed to fetch JWKS from %s after %d attempts: %s", jwks_url, max_retries, last_error)
    should_create, reason = await evaluate_incident_creation("Auth JWKS")
    if should_create:
        logger.warning("PagerDuty incident allowed for Auth JWKS reason=%s", reason)
        await create_pagerduty_incident(
            title="Auth JWKS endpoint unreachable",
            details=(
                "Auth middleware failed to fetch JWKS after 3 attempts and has no cache fallback. "
                f"JWKS URL: {jwks_url}. Last error: {last_error}"
            ),
        )
    else:
        logger.info("PagerDuty incident suppressed for Auth JWKS reason=%s", reason)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Authentication service temporarily unavailable",
    )


def _get_signing_key(jwks: dict, token: str) -> str:
    """
    Find the correct signing key from JWKS based on the token's kid header.
    
    Args:
        jwks: The JWKS dictionary
        token: The JWT token string
        
    Returns:
        The PEM-encoded public key string
    """
    from jose import jwk
    
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
            return settings.SUPABASE_JWT_SECRET
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing key ID",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            # Convert JWK to PEM format for verification
            return jwk.construct(key).to_pem().decode('utf-8')
    
    # Key not found - clear cache and fail
    global _jwks_cache, _jwks_cache_fetched_at
    _jwks_cache = None
    _jwks_cache_fetched_at = None
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

    # Fast-path: return cached user (avoids an admin DB session = ~3 round trips)
    cached: User | None = _cache_get_user(user_uuid)
    if cached is not None:
        if cached.status == "crm_only":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Please sign up to use Revtops",
            )
        return cached

    # Cache miss — hit DB
    from models.database import get_admin_session
    async with get_admin_session() as session:
        user: Optional[User] = await session.get(User, user_uuid)
        
        if not user:
            # Fallback: look up by email from JWT payload.
            # This handles users who were created (e.g. via waitlist/invite) with a
            # different DB ID before they signed in via OAuth with a new Supabase ID.
            email: Optional[str] = payload.get("email")
            if email:
                result = await session.execute(
                    select(User).where(User.email == email)
                )
                user = result.scalar_one_or_none()
                if user:
                    logger.warning(
                        f"User found by email fallback: JWT sub={sub}, "
                        f"DB id={user.id}, email={email}. "
                        f"IDs should be aligned via /auth/users/sync."
                    )
            
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

        _cache_set_user(user.id, user)
        return user


async def _resolve_active_organization_id(
    user: User,
    requested_org_id_raw: Optional[str],
) -> Optional[UUID]:
    """
    Resolve the active organization for this request.

    If the client sends a non-empty org id (header or WebSocket query), validate it:
    - Guest users: must match user.guest_organization_id (their single org).
    - Regular users: must have an active org_members row for that org.

    If the client omits the hint: guests default to guest_organization_id; others None.
    """
    trimmed: str = (requested_org_id_raw or "").strip()
    if not trimmed:
        if user.is_guest:
            return user.guest_organization_id
        return None

    try:
        requested: UUID = UUID(trimmed)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid organization id",
        )

    if user.is_guest:
        if user.guest_organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Guest user has no organization context",
            )
        if requested != user.guest_organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organization does not match guest context",
            )
        return user.guest_organization_id

    # Fast-path: cached membership check (avoids an admin DB session = ~3 round trips)
    cached_membership: bool | None = _cache_get_org_membership(user.id, requested)
    if cached_membership is True:
        return requested
    if cached_membership is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this organization or membership inactive",
        )

    from models.database import get_admin_session

    async with get_admin_session() as session:
        result = await session.execute(
            select(OrgMember).where(
                OrgMember.user_id == user.id,
                OrgMember.organization_id == requested,
                OrgMember.status == "active",
            )
        )
        membership: Optional[OrgMember] = result.scalar_one_or_none()

    is_member: bool = membership is not None
    _cache_set_org_membership(user.id, requested, is_member)

    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this organization or membership inactive",
        )
    return requested


async def _resolve_default_org_for_masquerade_user(user: User) -> Optional[UUID]:
    """Return the earliest active membership org for a masqueraded user.

    This avoids requests running without org context (and thus without RLS)
    when global admins impersonate regular users but the client has not yet
    hydrated and sent X-Organization-Id.
    """
    from models.database import get_admin_session

    async with get_admin_session() as session:
        first_org_row = await session.execute(
            select(OrgMember.organization_id)
            .where(
                OrgMember.user_id == user.id,
                OrgMember.status == "active",
            )
            .order_by(
                OrgMember.joined_at.asc().nulls_last(),
                OrgMember.created_at.asc().nulls_last(),
            )
            .limit(1)
        )
        return first_org_row.scalar_one_or_none()


async def get_current_auth(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    masquerade_user_id: Optional[str] = Header(None, alias="X-Masquerade-User-Id"),
    admin_user_id: Optional[str] = Header(None, alias="X-Admin-User-Id"),
    x_organization_id: Optional[str] = Header(None, alias="X-Organization-Id"),
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

    if masquerade_user_id:
        try:
            target_user_uuid = UUID(masquerade_user_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid masquerade user ID",
            )

        if admin_user_id and admin_user_id != str(user.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin header does not match authenticated user",
            )

        if not (user.role == "global_admin" or "global_admin" in (user.roles or [])):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only global admins can masquerade",
            )

        from models.database import get_admin_session
        async with get_admin_session() as session:
            target_user: Optional[User] = await session.get(User, target_user_uuid)

        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Masquerade user not found",
            )

        logger.info(
            "Masquerade auth context applied: admin=%s target=%s",
            user.id,
            target_user.id,
        )

        user = target_user

    is_global_admin = user.role == "global_admin" or "global_admin" in (user.roles or [])

    resolved_org_id: Optional[UUID] = await _resolve_active_organization_id(user, x_organization_id)
    if masquerade_user_id and resolved_org_id is None and not user.is_guest:
        resolved_org_id = await _resolve_default_org_for_masquerade_user(user)

    return AuthContext(
        user_id=user.id,
        organization_id=resolved_org_id,
        email=user.email,
        role=user.role or "user",
        is_global_admin=is_global_admin,
    )


async def get_optional_auth(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_organization_id: Optional[str] = Header(None, alias="X-Organization-Id"),
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
        is_global_admin = user.role == "global_admin" or "global_admin" in (user.roles or [])
        resolved_org_id: Optional[UUID] = await _resolve_active_organization_id(
            user, x_organization_id
        )
        return AuthContext(
            user_id=user.id,
            organization_id=resolved_org_id,
            email=user.email,
            role=user.role or "user",
            is_global_admin=is_global_admin,
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
        org_id_param: Optional[str] = websocket.query_params.get("org_id")
        resolved_org_id: Optional[UUID] = await _resolve_active_organization_id(
            user, org_id_param
        )

        return AuthContext(
            user_id=user.id,
            organization_id=resolved_org_id,
            email=user.email,
            role=user.role or "user",
            is_global_admin=user.role == "global_admin",
        )
    except HTTPException as e:
        close_code = 1013 if e.status_code >= 500 else 4001
        await websocket.close(code=close_code, reason=str(e.detail))
        raise

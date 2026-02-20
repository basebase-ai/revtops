"""
Penny Apps API routes.

Provides endpoints to:
- Execute named queries from an app's server-side spec
- Mint short-lived, app-scoped tokens
- List / get apps for the organization
- Generate embed tokens
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel
from sqlalchemy import func, select, text

from access_control import RightsContext, check_sql
from api.auth_middleware import AuthContext, require_organization
from config import settings
from models.app import App
from models.database import get_session, get_admin_session
from models.organization import Organization
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class QueryResponse(BaseModel):
    data: list[dict[str, Any]]
    columns: list[str]


class AppTokenResponse(BaseModel):
    token: str
    expires_at: str
    app_id: str
    api_base: str


class AppListItem(BaseModel):
    id: str
    title: str | None
    description: str | None
    created_at: str | None
    creator_name: str | None
    creator_email: str | None
    conversation_id: str | None


class AppListResponse(BaseModel):
    apps: list[AppListItem]
    total: int


class EmbedTokenResponse(BaseModel):
    embed_url: str
    token: str
    expires_at: str


# ---------------------------------------------------------------------------
# In-memory token store (MVP – swap for Redis / DB in production)
# ---------------------------------------------------------------------------

_TOKEN_TTL_SECONDS: int = 3600  # 1 hour
_EMBED_TOKEN_TTL_SECONDS: int = 30 * 24 * 3600  # 30 days

# token_string -> {app_id, organization_id, expires}
_app_tokens: dict[str, dict[str, Any]] = {}


def _mint_app_token(
    app_id: str,
    organization_id: str,
    ttl: int = _TOKEN_TTL_SECONDS,
) -> tuple[str, float]:
    """Create a short-lived, app-scoped bearer token."""
    token: str = f"rvapp_{secrets.token_urlsafe(32)}"
    expires: float = time.time() + ttl
    _app_tokens[token] = {
        "app_id": app_id,
        "organization_id": organization_id,
        "expires": expires,
    }
    return token, expires


def _verify_app_token(token: str, app_id: str) -> str:
    """Verify an app token and return the organization_id, or raise 401."""
    info: dict[str, Any] | None = _app_tokens.get(token)
    if info is None:
        raise HTTPException(status_code=401, detail="Invalid app token")
    if time.time() > info["expires"]:
        _app_tokens.pop(token, None)
        raise HTTPException(status_code=401, detail="App token expired")
    if info["app_id"] != app_id:
        raise HTTPException(status_code=403, detail="Token not valid for this app")
    return info["organization_id"]


def _cleanup_expired_tokens() -> None:
    """Evict expired tokens (called lazily)."""
    now: float = time.time()
    expired: list[str] = [k for k, v in _app_tokens.items() if now > v["expires"]]
    for k in expired:
        _app_tokens.pop(k, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_DANGEROUS_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)


def _validate_sql_is_select(sql: str) -> None:
    """Raise if the SQL is not a plain SELECT statement."""
    if not _SELECT_RE.match(sql):
        raise ValueError("Only SELECT queries are allowed")
    if _DANGEROUS_RE.search(sql):
        raise ValueError("Query contains disallowed SQL keywords")


def _json_serial(obj: Any) -> Any:
    """JSON serializer for types not handled by default."""
    if isinstance(obj, datetime):
        return f"{obj.isoformat()}Z"
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/{app_id}/token", response_model=AppTokenResponse)
async def create_app_token(
    app_id: str,
    auth: AuthContext = Depends(require_organization),
) -> AppTokenResponse:
    """Mint a short-lived token scoped to a single app's queries."""
    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    assert auth.organization_id_str is not None
    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(
            select(App).where(App.id == app_uuid)
        )
        app: App | None = result.scalar_one_or_none()
        if app is None:
            raise HTTPException(status_code=404, detail="App not found")

    token, expires = _mint_app_token(app_id, auth.organization_id_str)

    frontend_url: str = settings.FRONTEND_URL
    api_base: str = (
        "https://api.revtops.com/api"
        if "revtops" in frontend_url or "railway" in frontend_url
        else "/api"
    )

    return AppTokenResponse(
        token=token,
        expires_at=datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
        app_id=app_id,
        api_base=api_base,
    )


@router.post("/{app_id}/queries/{query_name}", response_model=QueryResponse)
async def execute_app_query(
    app_id: str,
    query_name: str,
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> QueryResponse:
    """Execute a named query from the app's server-side spec."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing app token")
    token: str = authorization.split(" ", 1)[1]

    organization_id: str = _verify_app_token(token, app_id)

    if len(_app_tokens) > 500:
        _cleanup_expired_tokens()

    try:
        raw_body: bytes = await request.body()
        params: dict[str, Any] = json.loads(raw_body) if raw_body else {}
    except (json.JSONDecodeError, ValueError):
        params = {}

    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(App).where(App.id == app_uuid)
        )
        app: App | None = result.scalar_one_or_none()
        if app is None:
            raise HTTPException(status_code=404, detail="App not found")

        queries: dict[str, Any] = app.queries or {}
        query_spec: dict[str, Any] | None = queries.get(query_name)

        if query_spec is None:
            raise HTTPException(
                status_code=404,
                detail=f"Query '{query_name}' not found in app spec",
            )

        sql: str = query_spec.get("sql", "")
        _validate_sql_is_select(sql)

        # Validate params against declared schema
        param_defs: dict[str, Any] = query_spec.get("params", {})
        bound_params: dict[str, Any] = {"org_id": organization_id}
        for pname, pdef in param_defs.items():
            value: Any = params.get(pname)
            if value is None and pdef.get("required", False):
                raise HTTPException(
                    status_code=400,
                    detail=f"Missing required parameter: {pname}",
                )
            if value is not None:
                bound_params[pname] = value

        # Inject LIMIT if not present
        sql_upper: str = sql.upper()
        if "LIMIT" not in sql_upper:
            sql = f"{sql.rstrip().rstrip(';')} LIMIT 5000"

        rights_ctx = RightsContext(
            organization_id=organization_id,
            user_id=None,
            conversation_id=None,
            is_workflow=False,
        )
        rights_result = await check_sql(rights_ctx, sql, bound_params)
        if not rights_result.allowed:
            raise HTTPException(
                status_code=403,
                detail=rights_result.deny_reason or "Query not allowed",
            )
        query_to_run: str = (
            rights_result.transformed_query if rights_result.transformed_query is not None else sql
        )
        params_to_use: dict[str, Any] = (
            rights_result.transformed_params if rights_result.transformed_params is not None else bound_params
        )

        try:
            raw_result = await session.execute(text(query_to_run), params_to_use)
            rows = raw_result.mappings().all()
            columns: list[str] = list(raw_result.keys()) if rows else []

            data: list[dict[str, Any]] = [
                {
                    k: _json_serial(v)
                    if not isinstance(v, (str, int, float, bool, type(None)))
                    else v
                    for k, v in dict(row).items()
                }
                for row in rows
            ]

            return QueryResponse(data=data, columns=columns)
        except Exception as exc:
            logger.error("App query execution failed: %s", exc)
            raise HTTPException(status_code=400, detail=f"Query error: {exc}")


# ---------------------------------------------------------------------------
# List / Get Apps
# ---------------------------------------------------------------------------


@router.get("", response_model=AppListResponse)
async def list_apps(
    auth: AuthContext = Depends(require_organization),
) -> AppListResponse:
    """List all apps for the current organization."""
    assert auth.organization_id_str is not None
    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(
            select(App).order_by(App.created_at.desc())
        )
        apps: list[App] = list(result.scalars().all())

        user_ids: set[UUID] = {a.user_id for a in apps}
        users_map: dict[UUID, User] = {}
        if user_ids:
            async with get_admin_session() as admin_sess:
                user_result = await admin_sess.execute(
                    select(User).where(User.id.in_(user_ids))
                )
                for u in user_result.scalars().all():
                    users_map[u.id] = u

        items: list[AppListItem] = []
        for a in apps:
            creator: User | None = users_map.get(a.user_id)
            items.append(
                AppListItem(
                    id=str(a.id),
                    title=a.title,
                    description=a.description,
                    created_at=f"{a.created_at.isoformat()}Z" if a.created_at else None,
                    creator_name=creator.name if creator else None,
                    creator_email=creator.email if creator else None,
                    conversation_id=str(a.conversation_id) if a.conversation_id else None,
                )
            )

        return AppListResponse(apps=items, total=len(items))


# ---------------------------------------------------------------------------
# Home App (must be before /{app_id} to avoid path collision)
# ---------------------------------------------------------------------------


class SetHomeAppRequest(BaseModel):
    app_id: str | None = None


@router.get("/home")
async def get_home_app(
    auth: AuthContext = Depends(require_organization),
) -> dict[str, Any]:
    """Get the organization's home app (or null) plus app_count for the org."""
    assert auth.organization_id_str is not None
    logger.info("[home] org=%s user=%s", auth.organization_id_str, auth.user_id_str)

    async with get_admin_session() as session:
        result = await session.execute(
            select(Organization).where(
                Organization.id == UUID(auth.organization_id_str)
            )
        )
        org: Organization | None = result.scalar_one_or_none()
        home_app_id = org.home_app_id if org else None

    async with get_session(organization_id=auth.organization_id_str) as session:
        count_result = await session.execute(select(func.count()).select_from(App))
        app_count: int = count_result.scalar_one()

        logger.info("[home] org=%s home_app_id=%s app_count=%d", auth.organization_id_str, home_app_id, app_count)

        if home_app_id is None:
            return {"app": None, "app_count": app_count}

        result = await session.execute(
            select(App).where(App.id == home_app_id)
        )
        app: App | None = result.scalar_one_or_none()
        if app is None:
            return {"app": None, "app_count": app_count}

        return {
            "app": {
                "id": str(app.id),
                "title": app.title,
                "description": app.description,
                "frontendCode": app.frontend_code,
            },
            "app_count": app_count,
        }


@router.patch("/home")
async def set_home_app(
    body: SetHomeAppRequest,
    auth: AuthContext = Depends(require_organization),
) -> dict[str, Any]:
    """Set or clear the organization's home app."""
    assert auth.organization_id_str is not None

    # If setting an app, validate it belongs to this org
    if body.app_id is not None:
        try:
            app_uuid = UUID(body.app_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid app ID")

        async with get_session(organization_id=auth.organization_id_str) as session:
            result = await session.execute(
                select(App).where(App.id == app_uuid)
            )
            app: App | None = result.scalar_one_or_none()
            if app is None:
                raise HTTPException(status_code=404, detail="App not found")

    # Update the organization
    async with get_admin_session() as session:
        result = await session.execute(
            select(Organization).where(
                Organization.id == UUID(auth.organization_id_str)
            )
        )
        org: Organization | None = result.scalar_one_or_none()
        if org is None:
            raise HTTPException(status_code=404, detail="Organization not found")

        org.home_app_id = UUID(body.app_id) if body.app_id else None
        await session.commit()

    return {"status": "success", "home_app_id": body.app_id}


# ---------------------------------------------------------------------------
# Get Single App
# ---------------------------------------------------------------------------


@router.get("/{app_id}")
async def get_app(
    app_id: str,
    auth: AuthContext = Depends(require_organization),
) -> dict[str, Any]:
    """Get a single app with its config (frontend_code only, not queries)."""
    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    assert auth.organization_id_str is not None
    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(
            select(App).where(App.id == app_uuid)
        )
        app: App | None = result.scalar_one_or_none()
        if app is None:
            raise HTTPException(status_code=404, detail="App not found")

        return {
            "id": str(app.id),
            "title": app.title,
            "description": app.description,
            "frontend_code": app.frontend_code,
            "query_names": list((app.queries or {}).keys()),
            "conversation_id": str(app.conversation_id) if app.conversation_id else None,
            "created_at": f"{app.created_at.isoformat()}Z" if app.created_at else None,
            "user_id": str(app.user_id),
        }


# ---------------------------------------------------------------------------
# Embed token
# ---------------------------------------------------------------------------


@router.get("/{app_id}/embed-data")
async def get_app_embed_data(
    app_id: str,
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> dict[str, Any]:
    """
    Get app data for embed rendering using an app-scoped token.

    Unlike GET /{app_id} which requires a Supabase JWT, this endpoint
    accepts the short-lived app token used by the embed page.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing app token")
    token: str = authorization.split(" ", 1)[1]

    organization_id: str = _verify_app_token(token, app_id)

    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(App).where(App.id == app_uuid)
        )
        app: App | None = result.scalar_one_or_none()
        if app is None:
            raise HTTPException(status_code=404, detail="App not found")

        return {
            "id": str(app.id),
            "title": app.title,
            "frontend_code": app.frontend_code,
        }


@router.post("/{app_id}/embed-token", response_model=EmbedTokenResponse)
async def create_embed_token(
    app_id: str,
    auth: AuthContext = Depends(require_organization),
) -> EmbedTokenResponse:
    """Generate a long-lived embed token for iframe usage."""
    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    assert auth.organization_id_str is not None
    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(
            select(App).where(App.id == app_uuid)
        )
        app: App | None = result.scalar_one_or_none()
        if app is None:
            raise HTTPException(status_code=404, detail="App not found")

    token, expires = _mint_app_token(
        app_id, auth.organization_id_str, ttl=_EMBED_TOKEN_TTL_SECONDS
    )

    frontend_url: str = settings.FRONTEND_URL.rstrip("/")
    embed_url: str = f"{frontend_url}/embed/{app_id}?token={token}"

    return EmbedTokenResponse(
        embed_url=embed_url,
        token=token,
        expires_at=datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
    )

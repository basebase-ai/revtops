"""
Basebase Apps API routes.

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

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request
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
    archived_at: str | None = None
    widget_config: dict[str, Any] | None = None


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

def _strip_screenshot(widget_config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Remove large screenshot data URL from list responses to avoid massive payloads."""
    if not widget_config or "screenshot" not in widget_config:
        return widget_config
    config = dict(widget_config)
    config["has_screenshot"] = True
    del config["screenshot"]
    return config


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
        if obj.tzinfo is not None:
            return obj.isoformat()
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
        "https://api.basebase.com/api"
        if "basebase.com" in frontend_url or "railway" in frontend_url
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
    archived: bool = Query(False),
    auth: AuthContext = Depends(require_organization),
) -> AppListResponse:
    """List all apps for the current organization."""
    assert auth.organization_id_str is not None
    async with get_session(organization_id=auth.organization_id_str) as session:
        stmt = select(App).order_by(App.created_at.desc())
        if archived:
            stmt = stmt.where(App.archived_at.isnot(None))
        else:
            stmt = stmt.where(App.archived_at.is_(None))
        result = await session.execute(stmt)
        apps: list[App] = list(result.scalars().all())

        user_ids: set[UUID] = {a.user_id for a in apps}
        users_map: dict[UUID, User] = {}
        if user_ids:
            user_result = await session.execute(
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
                    archived_at=f"{a.archived_at.isoformat()}Z" if a.archived_at else None,
                    widget_config=_strip_screenshot(a.widget_config),
                )
            )

        return AppListResponse(apps=items, total=len(items))


# ---------------------------------------------------------------------------
# Archive / Unarchive
# ---------------------------------------------------------------------------


@router.post("/{app_id}/archive")
async def archive_app(
    app_id: str,
    auth: AuthContext = Depends(require_organization),
) -> dict[str, str]:
    """Archive an app (soft-hide from gallery)."""
    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    assert auth.organization_id_str is not None
    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(select(App).where(App.id == app_uuid))
        app: App | None = result.scalar_one_or_none()
        if app is None:
            raise HTTPException(status_code=404, detail="App not found")
        # apps.archived_at is stored as a timestamp without timezone in Postgres.
        # Persist a naive UTC datetime to avoid asyncpg offset-aware/naive errors.
        app.archived_at = datetime.utcnow()
        logger.info("Archived app %s at %s", app_id, app.archived_at.isoformat())
        await session.commit()

    return {"status": "ok"}


@router.post("/{app_id}/unarchive")
async def unarchive_app(
    app_id: str,
    auth: AuthContext = Depends(require_organization),
) -> dict[str, str]:
    """Unarchive an app (restore to gallery)."""
    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    assert auth.organization_id_str is not None
    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(select(App).where(App.id == app_uuid))
        app: App | None = result.scalar_one_or_none()
        if app is None:
            raise HTTPException(status_code=404, detail="App not found")
        app.archived_at = None
        logger.info("Unarchived app %s", app_id)
        await session.commit()

    return {"status": "ok"}


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
                "frontendCodeCompiled": app.frontend_code_compiled,
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
# Widgets (must be before /{app_id} to avoid path collision)
# ---------------------------------------------------------------------------


@router.get("/widgets/all")
async def list_widgets(
    auth: AuthContext = Depends(require_organization),
) -> dict[str, Any]:
    """Return all non-archived apps that have a widget_config."""
    assert auth.organization_id_str is not None
    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(
            select(App)
            .where(App.archived_at.is_(None), App.widget_config.isnot(None))
            .order_by(App.updated_at.desc().nullslast())
        )
        apps = list(result.scalars().all())

    return {
        "widgets": [
            {
                "id": str(a.id),
                "title": a.title,
                "widget_config": _strip_screenshot(a.widget_config),
            }
            for a in apps
        ]
    }


@router.get("/widgets/{app_id}/screenshot")
async def get_app_screenshot(
    app_id: str,
    auth: AuthContext = Depends(require_organization),
) -> dict[str, Any]:
    """Fetch a single app's screenshot data URL on demand (not included in list responses)."""
    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    assert auth.organization_id_str is not None
    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(select(App).where(App.id == app_uuid))
        app: App | None = result.scalar_one_or_none()
        if app is None:
            raise HTTPException(status_code=404, detail="App not found")

    screenshot = (app.widget_config or {}).get("screenshot")
    return {"screenshot": screenshot}


# ---------------------------------------------------------------------------
# Preview Settings (must be before /{app_id} to avoid path collision)
# ---------------------------------------------------------------------------


_VALID_PREVIEW_MODES = {"screenshot", "widget", "mini_app", "icon"}
_VALID_DETAIL_LEVELS = {"minimal", "standard", "detailed"}


class PreviewSettingsRequest(BaseModel):
    preferred_mode: str | None = None
    detail_level: str | None = None


@router.patch("/{app_id}/preview-settings")
async def update_preview_settings(
    app_id: str,
    body: PreviewSettingsRequest,
    auth: AuthContext = Depends(require_organization),
) -> dict[str, Any]:
    """Update preview mode / detail level in an app's widget_config."""
    from services.widget_inference import generate_widget_config

    if body.preferred_mode is not None and body.preferred_mode not in _VALID_PREVIEW_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid preferred_mode. Must be one of: {', '.join(sorted(_VALID_PREVIEW_MODES))}",
        )
    if body.detail_level is not None and body.detail_level not in _VALID_DETAIL_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid detail_level. Must be one of: {', '.join(sorted(_VALID_DETAIL_LEVELS))}",
        )

    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    assert auth.organization_id_str is not None
    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(select(App).where(App.id == app_uuid))
        app: App | None = result.scalar_one_or_none()
        if app is None:
            raise HTTPException(status_code=404, detail="App not found")

        config = dict(app.widget_config) if app.widget_config else {}
        old_detail = config.get("detail_level")

        if body.preferred_mode is not None:
            config["preferred_mode"] = body.preferred_mode
        if body.detail_level is not None:
            config["detail_level"] = body.detail_level

        # If detail_level changed and mode is widget, regenerate widget inline
        detail_changed = (
            body.detail_level is not None
            and body.detail_level != old_detail
            and config.get("preferred_mode", "widget") == "widget"
        )
        if detail_changed and config.get("layout"):
            # Run queries and regenerate
            queries: dict[str, Any] = app.queries or {}
            query_results: dict[str, list[dict[str, Any]]] = {}
            for qname, qspec in queries.items():
                sql: str = qspec.get("sql", "")
                try:
                    _validate_sql_is_select(sql)
                    bound: dict[str, Any] = {"org_id": auth.organization_id_str}
                    sql_upper = sql.upper()
                    if "LIMIT" not in sql_upper:
                        sql = f"{sql.rstrip().rstrip(';')} LIMIT 100"
                    raw = await session.execute(text(sql), bound)
                    rows = raw.mappings().all()
                    query_results[qname] = [
                        {
                            k: _json_serial(v)
                            if not isinstance(v, (str, int, float, bool, type(None)))
                            else v
                            for k, v in dict(row).items()
                        }
                        for row in rows
                    ]
                except Exception as exc:
                    logger.warning("Preview regen query %s failed for app %s: %s", qname, app_id, exc)
                    query_results[qname] = []

            try:
                new_config = await generate_widget_config(
                    app=app,
                    organization_id=auth.organization_id_str,
                    query_results=query_results,
                    user_prompt=config.get("widget_prompt"),
                    detail_level=body.detail_level,
                )
                # Preserve screenshot and preview settings
                if config.get("screenshot"):
                    new_config["screenshot"] = config["screenshot"]
                if body.preferred_mode is not None:
                    new_config["preferred_mode"] = body.preferred_mode
                elif config.get("preferred_mode"):
                    new_config["preferred_mode"] = config["preferred_mode"]
                new_config["detail_level"] = body.detail_level
                config = new_config
            except Exception as exc:
                logger.error("Widget regen failed for app %s: %s", app_id, exc)
                # Still save the settings even if regen fails

        app.widget_config = config
        await session.commit()

    return {"widget_config": _strip_screenshot(config)}


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
            "frontend_code_compiled": app.frontend_code_compiled,
            "query_names": list((app.queries or {}).keys()),
            "conversation_id": str(app.conversation_id) if app.conversation_id else None,
            "created_at": f"{app.created_at.isoformat()}Z" if app.created_at else None,
            "user_id": str(app.user_id),
            "widget_config": _strip_screenshot(app.widget_config),
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
            "frontend_code_compiled": app.frontend_code_compiled,
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


# ---------------------------------------------------------------------------
# Screenshots
# ---------------------------------------------------------------------------


class ScreenshotRequest(BaseModel):
    screenshot: str  # data URL


@router.post("/{app_id}/screenshot")
async def save_screenshot(
    app_id: str,
    body: ScreenshotRequest,
    auth: AuthContext = Depends(require_organization),
) -> dict[str, str]:
    """Store a screenshot data URL in the app's widget_config."""
    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    # Basic validation: must be a data URL, cap at ~2MB
    if not body.screenshot.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="Invalid screenshot data URL")
    if len(body.screenshot) > 2_000_000:
        raise HTTPException(status_code=400, detail="Screenshot too large (max 2MB)")

    assert auth.organization_id_str is not None
    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(select(App).where(App.id == app_uuid))
        app: App | None = result.scalar_one_or_none()
        if app is None:
            raise HTTPException(status_code=404, detail="App not found")

        config = dict(app.widget_config) if app.widget_config else {}
        config["screenshot"] = body.screenshot
        app.widget_config = config
        await session.commit()

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class GenerateWidgetRequest(BaseModel):
    prompt: str | None = None
    detail_level: str | None = None


@router.post("/{app_id}/widget")
async def generate_widget(
    app_id: str,
    body: GenerateWidgetRequest,
    auth: AuthContext = Depends(require_organization),
) -> dict[str, Any]:
    """Generate (or regenerate) a widget config for an app."""
    from services.widget_inference import generate_widget_config

    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    assert auth.organization_id_str is not None
    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(select(App).where(App.id == app_uuid))
        app: App | None = result.scalar_one_or_none()
        if app is None:
            raise HTTPException(status_code=404, detail="App not found")

        # Run the app's queries to get data for the LLM
        queries: dict[str, Any] = app.queries or {}
        query_results: dict[str, list[dict[str, Any]]] = {}
        for qname, qspec in queries.items():
            sql: str = qspec.get("sql", "")
            try:
                _validate_sql_is_select(sql)
                bound: dict[str, Any] = {"org_id": auth.organization_id_str}
                sql_upper = sql.upper()
                if "LIMIT" not in sql_upper:
                    sql = f"{sql.rstrip().rstrip(';')} LIMIT 100"
                raw = await session.execute(text(sql), bound)
                rows = raw.mappings().all()
                query_results[qname] = [
                    {
                        k: _json_serial(v)
                        if not isinstance(v, (str, int, float, bool, type(None)))
                        else v
                        for k, v in dict(row).items()
                    }
                    for row in rows
                ]
            except Exception as exc:
                logger.warning("Widget query %s failed for app %s: %s", qname, app_id, exc)
                query_results[qname] = []

        try:
            config = await generate_widget_config(
                app=app,
                organization_id=auth.organization_id_str,
                query_results=query_results,
                user_prompt=body.prompt,
                detail_level=body.detail_level or "standard",
            )
        except Exception as exc:
            logger.error("Widget inference failed for app %s: %s", app_id, exc)
            raise HTTPException(status_code=500, detail="Widget generation failed")

        # Preserve existing screenshot if present
        if app.widget_config and app.widget_config.get("screenshot"):
            config["screenshot"] = app.widget_config["screenshot"]

        app.widget_config = config
        await session.commit()

    return {"widget_config": config}


@router.delete("/{app_id}/widget")
async def delete_widget(
    app_id: str,
    auth: AuthContext = Depends(require_organization),
) -> dict[str, str]:
    """Clear an app's widget config."""
    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    assert auth.organization_id_str is not None
    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(select(App).where(App.id == app_uuid))
        app: App | None = result.scalar_one_or_none()
        if app is None:
            raise HTTPException(status_code=404, detail="App not found")
        app.widget_config = None
        await session.commit()

    return {"status": "ok"}

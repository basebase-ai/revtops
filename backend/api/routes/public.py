"""
Unauthenticated public reads for apps and artifacts marked visibility=public.

Uses admin session with explicit visibility filter (RLS bypass).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy import select

from config import settings
from models.app import App
from models.artifact import Artifact
from models.conversation import Conversation
from models.database import get_admin_session, get_session
from models.user import User
from services.app_query_runner import AppQueryResponse as QueryResponse, run_named_app_query
from services.public_previews import build_preview_html, decode_data_url_image, render_card_png

router = APIRouter()
share_router = APIRouter()
logger = logging.getLogger(__name__)

_PREVIEW_CACHE_TTL_SECONDS = 300
_PREVIEW_CACHE_MAX_ITEMS = 512
_preview_html_cache: dict[str, tuple[float, str]] = {}
_preview_image_cache: dict[str, tuple[float, bytes, str]] = {}
_UNFURLABLE_VISIBILITIES: frozenset[str] = frozenset({"private", "team", "public"})


def _cache_get_html(key: str) -> str | None:
    now = time.time()
    cached = _preview_html_cache.get(key)
    if cached is None:
        return None
    expires_at, html = cached
    if now > expires_at:
        _preview_html_cache.pop(key, None)
        return None
    return html


def _cache_set_html(key: str, html: str) -> None:
    if len(_preview_html_cache) >= _PREVIEW_CACHE_MAX_ITEMS:
        _preview_html_cache.pop(next(iter(_preview_html_cache)), None)
    _preview_html_cache[key] = (time.time() + _PREVIEW_CACHE_TTL_SECONDS, html)


def _cache_get_image(key: str) -> tuple[bytes, str] | None:
    now = time.time()
    cached = _preview_image_cache.get(key)
    if cached is None:
        return None
    expires_at, image_bytes, mime_type = cached
    if now > expires_at:
        _preview_image_cache.pop(key, None)
        return None
    return image_bytes, mime_type


def _cache_set_image(key: str, image_bytes: bytes, mime_type: str) -> None:
    if len(_preview_image_cache) >= _PREVIEW_CACHE_MAX_ITEMS:
        _preview_image_cache.pop(next(iter(_preview_image_cache)), None)
    _preview_image_cache[key] = (
        time.time() + _PREVIEW_CACHE_TTL_SECONDS,
        image_bytes,
        mime_type,
    )


def _is_unfurlable_visibility(visibility: str | None) -> bool:
    return visibility in _UNFURLABLE_VISIBILITIES


@router.get("/apps/{app_id}")
async def get_public_app(app_id: str) -> dict[str, Any]:
    """Return app frontend payload for public (no JWT) viewers."""
    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    async with get_admin_session() as session:
        result = await session.execute(
            select(App).where(App.id == app_uuid, App.visibility == "public")
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
        "visibility": "public",
    }


@router.post("/apps/{app_id}/queries/{query_name}", response_model=QueryResponse)
async def execute_public_app_query(
    app_id: str,
    query_name: str,
    request: Request,
) -> QueryResponse:
    """Run a named query for a public app (org-scoped data; SELECT-only)."""
    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    try:
        raw_body: bytes = await request.body()
        params: dict[str, Any] = json.loads(raw_body) if raw_body else {}
    except (json.JSONDecodeError, ValueError):
        params = {}

    async with get_admin_session() as session:
        result = await session.execute(
            select(App).where(App.id == app_uuid, App.visibility == "public")
        )
        app: App | None = result.scalar_one_or_none()

    if app is None:
        raise HTTPException(status_code=404, detail="App not found")

    org_id: str = str(app.organization_id)

    async with get_session(organization_id=org_id) as session:
        return await run_named_app_query(
            app=app,
            organization_id=org_id,
            query_name=query_name,
            params=params,
            session=session,
        )


class PublicArtifactResponse(BaseModel):
    id: str
    type: str | None
    title: str | None
    description: str | None
    content_type: str | None
    mime_type: str | None
    filename: str | None
    content: str | None
    conversation_id: str | None
    message_id: str | None
    created_at: str | None
    user_id: str | None
    visibility: str


@router.get("/artifacts/{artifact_id}", response_model=PublicArtifactResponse)
async def get_public_artifact(artifact_id: str) -> PublicArtifactResponse:
    """Full artifact content for public viewers."""
    try:
        artifact_uuid = UUID(artifact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid artifact ID format")

    async with get_admin_session() as session:
        result = await session.execute(
            select(Artifact).where(
                Artifact.id == artifact_uuid,
                Artifact.visibility == "public",
            )
        )
        artifact: Artifact | None = result.scalar_one_or_none()

    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    return PublicArtifactResponse(
        id=str(artifact.id),
        type=artifact.type,
        title=artifact.title,
        description=artifact.description,
        content_type=artifact.content_type,
        mime_type=artifact.mime_type,
        filename=artifact.filename,
        content=artifact.content,
        conversation_id=str(artifact.conversation_id) if artifact.conversation_id else None,
        message_id=str(artifact.message_id) if artifact.message_id else None,
        created_at=f"{artifact.created_at.isoformat()}Z" if artifact.created_at else None,
        user_id=str(artifact.user_id) if artifact.user_id else None,
        visibility="public",
    )


def _frontend_origin() -> str:
    return settings.FRONTEND_URL.rstrip("/")


def _public_origin(request: Request) -> str:
    """Best-effort absolute origin for public preview assets behind proxies."""
    configured = (settings.BACKEND_PUBLIC_URL or "").strip().rstrip("/")
    if configured:
        return configured

    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    scheme = forwarded_proto or request.url.scheme or "https"
    host = forwarded_host or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}".rstrip("/")


def _owner_label(user: User | None) -> str:
    """Return a compact owner label suitable for public descriptions."""
    if user is None:
        return "Unknown owner"
    if user.name:
        return user.name
    if user.email:
        return user.email
    return "Unknown owner"


def _public_preview_description(
    *,
    conversation: Conversation | None,
    app: App | None = None,
    artifact: Artifact | None = None,
    owner: User | None,
) -> str:
    """Build a concise public description for social preview unfurls."""
    owner_label = _owner_label(owner)
    app_description = (getattr(app, "description", None) or "").strip() if app else ""
    if app_description:
        return f"{app_description} — {owner_label}"
    if conversation and conversation.title:
        return f"{conversation.title} — {owner_label}"
    if app and app.title:
        return f"{app.title} — {owner_label}"
    if artifact and artifact.title:
        return f"{artifact.title} — {owner_label}"
    if artifact:
        return f"Document — {owner_label}"
    return f"Application — {owner_label}"


def _public_preview_title(*, app: App | None = None, artifact: Artifact | None = None) -> str:
    """Build a specific page title so social previews never look generic."""
    if app and app.title:
        return f"{app.title} · Basebase"
    if artifact and artifact.title:
        return f"{artifact.title} · Basebase"
    if app:
        return "Shared App · Basebase"
    if artifact:
        return "Shared Document · Basebase"
    return "Basebase"


@router.get("/share/apps/{app_id}", response_class=HTMLResponse)
@share_router.get("/basebase/apps/{app_id}", response_class=HTMLResponse)
async def get_public_app_share_preview(app_id: str, request: Request) -> HTMLResponse:
    """HTML metadata endpoint used by Slack + external scrapers for public app links."""
    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    async with get_admin_session() as session:
        result = await session.execute(select(App).where(App.id == app_uuid))
        app: App | None = result.scalar_one_or_none()
    if app is None or not _is_unfurlable_visibility(app.visibility):
        raise HTTPException(status_code=404, detail="App not found")
    if app.visibility != "public":
        logger.info(
            "[public_preview] rendering non-public app unfurl app_id=%s visibility=%s",
            app_id,
            app.visibility,
        )

    logger.info("[public_preview] rendering app preview app_id=%s", app_id)
    app_updated_at = app.updated_at.isoformat() if app.updated_at else "none"
    html_cache_key = f"app_preview:{app_id}:{app_updated_at}"
    cached_html = _cache_get_html(html_cache_key)
    if cached_html is not None:
        logger.info("[public_preview] app preview cache hit app_id=%s", app_id)
        return HTMLResponse(
            content=cached_html,
            headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=60"},
        )

    logger.info("[public_preview] app preview cache miss app_id=%s", app_id)
    conversation: Conversation | None = None
    owner: User | None = None
    async with get_admin_session() as session:
        if app.conversation_id:
            conversation = await session.scalar(
                select(Conversation).where(Conversation.id == app.conversation_id)
            )
        owner = await session.scalar(select(User).where(User.id == app.user_id))

    canonical_url = f"{_frontend_origin()}/basebase/apps/{app_id}"
    redirect_url = canonical_url
    image_url = f"{_public_origin(request)}/api/public/share/apps/{app_id}/snapshot.png"
    title = _public_preview_title(app=app)
    description = _public_preview_description(conversation=conversation, app=app, owner=owner)
    logger.info(
        "[public_preview] app metadata app_id=%s title=%s description=%s",
        app_id,
        title,
        description,
    )
    html = build_preview_html(
        page_title=title,
        description=description,
        canonical_url=canonical_url,
        image_url=image_url,
        redirect_url=redirect_url,
    )
    _cache_set_html(html_cache_key, html)
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=60"},
    )


@router.get("/share/apps/{app_id}/snapshot.png")
async def get_public_app_share_snapshot(app_id: str) -> Response:
    """Snapshot image used by link preview scrapers for shared app links."""
    try:
        app_uuid = UUID(app_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid app ID")

    async with get_admin_session() as session:
        result = await session.execute(select(App).where(App.id == app_uuid))
        app: App | None = result.scalar_one_or_none()
    if app is None or not _is_unfurlable_visibility(app.visibility):
        raise HTTPException(status_code=404, detail="App not found")
    if app.visibility != "public":
        logger.info(
            "[public_preview] serving non-public app snapshot app_id=%s visibility=%s",
            app_id,
            app.visibility,
        )
    app_updated_at = app.updated_at.isoformat() if app.updated_at else "none"
    image_cache_key = f"app_snapshot:{app_id}:{app_updated_at}"
    cached_image = _cache_get_image(image_cache_key)
    if cached_image is not None:
        image_bytes, mime_type = cached_image
        logger.info("[public_preview] app snapshot cache hit app_id=%s mime=%s", app_id, mime_type)
        return Response(
            content=image_bytes,
            media_type=mime_type,
            headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=60"},
        )

    screenshot_data_url: str | None = (app.widget_config or {}).get("screenshot")
    decoded = decode_data_url_image(screenshot_data_url)
    if decoded is not None:
        image_bytes, mime_type = decoded
        logger.info("[public_preview] serving app screenshot app_id=%s mime=%s", app_id, mime_type)
        _cache_set_image(image_cache_key, image_bytes, mime_type)
        return Response(
            content=image_bytes,
            media_type=mime_type,
            headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=60"},
        )

    logger.info("[public_preview] app screenshot missing; serving generated card app_id=%s", app_id)
    image_bytes = render_card_png(
        heading="Basebase App",
        title=app.title or "Untitled App",
        description=app.description or "Interactive app shared from Basebase.",
        footer=f"App ID: {app_id}",
    )
    _cache_set_image(image_cache_key, image_bytes, "image/png")
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=60"},
    )


@router.get("/share/artifacts/{artifact_id}", response_class=HTMLResponse)
@share_router.get("/basebase/documents/{artifact_id}", response_class=HTMLResponse)
@share_router.get("/basebase/artifacts/{artifact_id}", response_class=HTMLResponse)
async def get_public_artifact_share_preview(artifact_id: str, request: Request) -> HTMLResponse:
    """HTML metadata endpoint used by Slack + external scrapers for public artifact links."""
    try:
        artifact_uuid = UUID(artifact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid artifact ID")

    async with get_admin_session() as session:
        result = await session.execute(select(Artifact).where(Artifact.id == artifact_uuid))
        artifact: Artifact | None = result.scalar_one_or_none()
    if artifact is None or not _is_unfurlable_visibility(artifact.visibility):
        raise HTTPException(status_code=404, detail="Artifact not found")
    if artifact.visibility != "public":
        logger.info(
            "[public_preview] rendering non-public artifact unfurl artifact_id=%s visibility=%s",
            artifact_id,
            artifact.visibility,
        )

    logger.info("[public_preview] rendering artifact preview artifact_id=%s", artifact_id)
    artifact_version = ":".join(
        [
            str(artifact.created_at.isoformat() if artifact.created_at else "none"),
            str(artifact.title or ""),
            str(artifact.description or ""),
            str(artifact.content_type or ""),
        ]
    )
    html_cache_key = f"artifact_preview:{artifact_id}:{artifact_version}"
    cached_html = _cache_get_html(html_cache_key)
    if cached_html is not None:
        logger.info("[public_preview] artifact preview cache hit artifact_id=%s", artifact_id)
        return HTMLResponse(
            content=cached_html,
            headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=60"},
        )
    logger.info("[public_preview] artifact preview cache miss artifact_id=%s", artifact_id)
    conversation: Conversation | None = None
    owner: User | None = None
    async with get_admin_session() as session:
        if artifact.conversation_id:
            conversation = await session.scalar(
                select(Conversation).where(Conversation.id == artifact.conversation_id)
            )
        if artifact.user_id:
            owner = await session.scalar(select(User).where(User.id == artifact.user_id))

    canonical_url = f"{_frontend_origin()}/basebase/documents/{artifact_id}"
    redirect_url = f"{_frontend_origin()}/public/artifacts/{artifact_id}"
    image_url = f"{_public_origin(request)}/api/public/share/artifacts/{artifact_id}/snapshot.png"
    title = _public_preview_title(artifact=artifact)
    description = _public_preview_description(conversation=conversation, artifact=artifact, owner=owner)
    logger.info(
        "[public_preview] artifact metadata artifact_id=%s title=%s description=%s",
        artifact_id,
        title,
        description,
    )
    html = build_preview_html(
        page_title=title,
        description=description[:240],
        canonical_url=canonical_url,
        image_url=image_url,
        redirect_url=redirect_url,
    )
    _cache_set_html(html_cache_key, html)
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=60"},
    )


@router.get("/share/artifacts/{artifact_id}/snapshot.png")
async def get_public_artifact_share_snapshot(artifact_id: str) -> Response:
    """Snapshot image used by link preview scrapers for shared artifact links."""
    try:
        artifact_uuid = UUID(artifact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid artifact ID")

    async with get_admin_session() as session:
        result = await session.execute(select(Artifact).where(Artifact.id == artifact_uuid))
        artifact: Artifact | None = result.scalar_one_or_none()
    if artifact is None or not _is_unfurlable_visibility(artifact.visibility):
        raise HTTPException(status_code=404, detail="Artifact not found")
    if artifact.visibility != "public":
        logger.info(
            "[public_preview] serving non-public artifact snapshot artifact_id=%s visibility=%s",
            artifact_id,
            artifact.visibility,
        )
    artifact_version = ":".join(
        [
            str(artifact.created_at.isoformat() if artifact.created_at else "none"),
            str(artifact.title or ""),
            str(artifact.description or ""),
            str(artifact.content_type or ""),
        ]
    )
    image_cache_key = f"artifact_snapshot:{artifact_id}:{artifact_version}"
    cached_image = _cache_get_image(image_cache_key)
    if cached_image is not None:
        image_bytes, mime_type = cached_image
        logger.info("[public_preview] artifact snapshot cache hit artifact_id=%s", artifact_id)
        return Response(
            content=image_bytes,
            media_type=mime_type,
            headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=60"},
        )

    summary = (artifact.description or artifact.content or "Shared artifact from Basebase.").replace("\n", " ")
    image_bytes = render_card_png(
        heading="Basebase Artifact",
        title=artifact.title or "Untitled Artifact",
        description=summary,
        footer=f"Type: {artifact.content_type or artifact.type or 'document'}",
    )
    logger.info("[public_preview] serving generated artifact snapshot artifact_id=%s", artifact_id)
    _cache_set_image(image_cache_key, image_bytes, "image/png")
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=60"},
    )

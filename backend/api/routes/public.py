"""
Unauthenticated public reads for apps and artifacts marked visibility=public.

Uses admin session with explicit visibility filter (RLS bypass).
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from models.app import App
from models.artifact import Artifact
from models.database import get_admin_session, get_session
from services.app_query_runner import AppQueryResponse as QueryResponse, run_named_app_query

router = APIRouter()


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

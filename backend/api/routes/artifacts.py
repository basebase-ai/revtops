"""
Artifact API routes for viewing and downloading artifacts.

SECURITY: All endpoints use JWT authentication via the AuthContext dependency.
User and organization are verified from the JWT token, NOT from query parameters.

Provides endpoints to:
- Get artifact metadata
- Get artifact content
- Download artifacts as files
- List artifacts in a conversation
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import or_, select

from api.auth_middleware import AuthContext, require_organization
from models.artifact import Artifact
from models.database import get_session
from models.user import User
from services.pdf_generator import generate_pdf

router = APIRouter()


class ArtifactMetadata(BaseModel):
    """Artifact metadata for list views."""

    id: str
    type: Optional[str]
    title: Optional[str]
    description: Optional[str]
    content_type: Optional[str]
    mime_type: Optional[str]
    filename: Optional[str]
    conversation_id: Optional[str]
    message_id: Optional[str]
    created_at: Optional[str]
    user_id: Optional[str]
    creator_name: Optional[str] = None


class ArtifactContent(BaseModel):
    """Full artifact with content."""

    id: str
    type: Optional[str]
    title: Optional[str]
    description: Optional[str]
    content_type: Optional[str]
    mime_type: Optional[str]
    filename: Optional[str]
    content: Optional[str]
    conversation_id: Optional[str]
    message_id: Optional[str]
    created_at: Optional[str]
    user_id: Optional[str]


class ArtifactListResponse(BaseModel):
    """List of artifacts."""

    artifacts: list[ArtifactMetadata]
    total: int


@router.get("", response_model=ArtifactListResponse)
async def list_artifacts(
    search: Optional[str] = Query(None),
    auth: AuthContext = Depends(require_organization),
) -> ArtifactListResponse:
    """
    List all artifacts for the current organization (most recent first).
    Optional search filters on title and description (case-insensitive).
    """
    async with get_session(organization_id=auth.organization_id_str) as session:
        stmt = select(Artifact).order_by(Artifact.created_at.desc())
        if search and search.strip():
            term: str = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Artifact.title.ilike(term),
                    Artifact.description.ilike(term),
                )
            )
        result = await session.execute(stmt)
        artifacts: list[Artifact] = list(result.scalars().all())

        user_ids: set[UUID] = {a.user_id for a in artifacts if a.user_id is not None}
        users_map: dict[UUID, User] = {}
        if user_ids:
            user_result = await session.execute(select(User).where(User.id.in_(user_ids)))
            for u in user_result.scalars().all():
                users_map[u.id] = u

        artifact_list: list[ArtifactMetadata] = [
            ArtifactMetadata(
                id=str(a.id),
                type=a.type,
                title=a.title,
                description=a.description,
                content_type=a.content_type,
                mime_type=a.mime_type,
                filename=a.filename,
                conversation_id=str(a.conversation_id) if a.conversation_id else None,
                message_id=str(a.message_id) if a.message_id else None,
                created_at=f"{a.created_at.isoformat()}Z" if a.created_at else None,
                user_id=str(a.user_id) if a.user_id else None,
                creator_name=(u.name if (u := users_map.get(a.user_id)) else None),
            )
            for a in artifacts
        ]

        return ArtifactListResponse(artifacts=artifact_list, total=len(artifact_list))


@router.get("/{artifact_id}", response_model=ArtifactContent)
async def get_artifact(
    artifact_id: str,
    auth: AuthContext = Depends(require_organization),
) -> ArtifactContent:
    """
    Get artifact by ID with full content.
    
    Args:
        artifact_id: UUID of the artifact
        auth: Verified authentication context (from JWT)
        
    Returns:
        Full artifact including content
    """
    try:
        artifact_uuid = UUID(artifact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid artifact ID format")

    # RLS (org_isolation) already restricts to auth.organization_id; allow any org member to view
    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(select(Artifact).where(Artifact.id == artifact_uuid))
        artifact: Artifact | None = result.scalar_one_or_none()

        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")

        return ArtifactContent(
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
            user_id=str(artifact.user_id),
        )


@router.get("/{artifact_id}/download", response_model=None)
async def download_artifact(
    artifact_id: str,
    format: Optional[str] = Query(None),
    auth: AuthContext = Depends(require_organization),
) -> Response:
    """
    Download artifact as a file.
    
    For PDF artifacts, generates PDF from stored markdown content.
    
    Args:
        artifact_id: UUID of the artifact
        auth: Verified authentication context (from JWT)
        
    Returns:
        File response with appropriate content-type and filename
    """
    try:
        artifact_uuid = UUID(artifact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid artifact ID format")

    # RLS (org_isolation) already restricts to auth.organization_id; allow any org member to download
    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(select(Artifact).where(Artifact.id == artifact_uuid))
        artifact: Artifact | None = result.scalar_one_or_none()

        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")

        if not artifact.content:
            raise HTTPException(status_code=404, detail="Artifact has no content")

        content_type: str = artifact.content_type or "text"
        filename: str = artifact.filename or f"artifact.{_get_extension(content_type)}"

        # When format=pdf is requested, generate PDF from any text-based content
        if format == "pdf" and content_type in ("markdown", "text", "pdf"):
            pdf_bytes: bytes = generate_pdf(artifact.content)
            pdf_filename: str = artifact.filename or "artifact.pdf"
            if not pdf_filename.endswith(".pdf"):
                pdf_filename = pdf_filename.rsplit(".", 1)[0] + ".pdf"
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="{pdf_filename}"',
                },
            )

        if format == "markdown" and content_type in ("markdown", "text", "pdf"):
            md_filename: str = artifact.filename or "artifact.md"
            if not md_filename.endswith(".md"):
                md_filename = md_filename.rsplit(".", 1)[0] + ".md"
            return Response(
                content=artifact.content.encode("utf-8"),
                media_type="text/markdown",
                headers={
                    "Content-Disposition": f'attachment; filename="{md_filename}"',
                },
            )

        # Fallback: route by stored content_type when no format override
        if content_type == "pdf":
            pdf_bytes_fallback: bytes = generate_pdf(artifact.content)
            return Response(
                content=pdf_bytes_fallback,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
            )
        
        elif content_type == "chart":
            html_content: str = _generate_chart_html(artifact.content, artifact.title or "Chart")
            if not filename.endswith(".html"):
                filename = filename.rsplit(".", 1)[0] + ".html"
            return Response(
                content=html_content.encode("utf-8"),
                media_type="text/html",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
            )
        
        elif content_type == "markdown":
            return Response(
                content=artifact.content.encode("utf-8"),
                media_type="text/markdown",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
            )
        
        else:
            return Response(
                content=artifact.content.encode("utf-8"),
                media_type="text/plain",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
            )


@router.get("/conversation/{conversation_id}", response_model=ArtifactListResponse)
async def list_conversation_artifacts(
    conversation_id: str,
    auth: AuthContext = Depends(require_organization),
) -> ArtifactListResponse:
    """
    List all artifacts in a conversation.
    
    Args:
        conversation_id: UUID of the conversation
        auth: Verified authentication context (from JWT)
        
    Returns:
        List of artifact metadata (without content)
    """
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID format")

    async with get_session(organization_id=auth.organization_id_str) as session:
        result = await session.execute(
            select(Artifact)
            .where(
                Artifact.conversation_id == conv_uuid,
                (Artifact.user_id == auth.user_id) | (Artifact.user_id.is_(None)),
            )
            .order_by(Artifact.created_at.desc())
        )
        artifacts: list[Artifact] = list(result.scalars().all())

        artifact_list = [
            ArtifactMetadata(
                id=str(a.id),
                type=a.type,
                title=a.title,
                description=a.description,
                content_type=a.content_type,
                mime_type=a.mime_type,
                filename=a.filename,
                conversation_id=str(a.conversation_id) if a.conversation_id else None,
                message_id=str(a.message_id) if a.message_id else None,
                created_at=f"{a.created_at.isoformat()}Z" if a.created_at else None,
                user_id=str(a.user_id) if a.user_id else None,
            )
            for a in artifacts
        ]

        return ArtifactListResponse(
            artifacts=artifact_list,
            total=len(artifact_list),
        )


def _get_extension(content_type: str) -> str:
    """Get file extension for content type."""
    extensions: dict[str, str] = {
        "text": "txt",
        "markdown": "md",
        "pdf": "pdf",
        "chart": "html",
    }
    return extensions.get(content_type, "txt")


def _generate_chart_html(plotly_json: str, title: str) -> str:
    """
    Generate standalone HTML file with embedded Plotly chart.
    
    Args:
        plotly_json: Plotly figure specification as JSON string
        title: Chart title for HTML page
        
    Returns:
        Complete HTML document as string
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        body {{
            margin: 0;
            padding: 20px;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #ffffff;
        }}
        #chart {{
            width: 100%;
            height: calc(100vh - 40px);
        }}
    </style>
</head>
<body>
    <div id="chart"></div>
    <script>
        const spec = {plotly_json};
        Plotly.newPlot('chart', spec.data, spec.layout || {{}}, {{responsive: true}});
    </script>
</body>
</html>"""

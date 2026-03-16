"""
Artifacts connector – create, read, and update downloadable artifacts (markdown, text, PDF, charts).

Built-in connector enabled by default for all orgs. Exposes artifacts as a data source
the agent can query (read) and write (create, update) so editing an artifact updates in place.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update

from config import settings
from connectors.base import BaseConnector
from connectors.registry import (
    AuthType,
    Capability,
    ConnectorMeta,
    ConnectorScope,
    WriteOperation,
)
from models.artifact import Artifact
from models.database import get_session

logger = logging.getLogger(__name__)

CONTENT_TYPE_TO_MIME: dict[str, str] = {
    "text": "text/plain",
    "markdown": "text/markdown",
    "pdf": "application/pdf",
    "chart": "application/json",
}


class ArtifactConnector(BaseConnector):
    """Create, read, and update downloadable artifacts (files)."""

    source_system: str = "artifacts"
    meta = ConnectorMeta(
        name="Artifacts",
        slug="artifacts",
        auth_type=AuthType.CUSTOM,
        scope=ConnectorScope.ORGANIZATION,
        capabilities=[Capability.QUERY, Capability.WRITE],
        query_description=(
            "Read an artifact by ID. Use format: 'read <artifact_id>' where artifact_id is the UUID. "
            "Returns title, content, content_type, filename so you can inspect it before editing."
        ),
        write_operations=[
            WriteOperation(
                name="create",
                entity_type="artifact",
                description="Create a new artifact. Use ONLY when the user wants something brand-new. If they say 'update', 'edit', 'change', or 'revise' an existing artifact, use 'update' instead.",
                parameters=[
                    {"name": "title", "type": "string", "required": True, "description": "Display title"},
                    {"name": "filename", "type": "string", "required": True, "description": "Filename with extension (e.g. report.md, chart.html)"},
                    {"name": "content_type", "type": "string", "required": True, "description": "One of: text, markdown, pdf, chart"},
                    {"name": "content", "type": "string", "required": True, "description": "File content (markdown for PDF, Plotly JSON for charts)"},
                ],
            ),
            WriteOperation(
                name="update",
                entity_type="artifact",
                description="Update an existing artifact in place. Use when the user asks to edit, revise, change, or add to an artifact. Same URL, no new link. Get artifact_id from the prior create/update result in this conversation.",
                parameters=[
                    {"name": "artifact_id", "type": "string", "required": True, "description": "UUID of the artifact to update"},
                    {"name": "content", "type": "string", "required": False, "description": "New content"},
                    {"name": "title", "type": "string", "required": False, "description": "New title"},
                    {"name": "filename", "type": "string", "required": False, "description": "New filename"},
                    {"name": "content_type", "type": "string", "required": False, "description": "New content_type (text, markdown, pdf, chart)"},
                ],
            ),
        ],
        description="Create and update downloadable files. IMPORTANT: When the user asks to edit, revise, or update an artifact they're viewing or that was just created, use operation='update' with the artifact_id from the previous result — do NOT create a new artifact.",
        usage_guide=(
            "When the user says 'update', 'edit', 'change', 'revise', or 'add to' regarding an artifact from this conversation, "
            "use operation='update' with artifact_id from your prior create/update result. Creating a new artifact gives a new URL; updating keeps the same one."
        ),
    )

    async def sync_deals(self) -> int:
        return 0

    async def sync_accounts(self) -> int:
        return 0

    async def sync_contacts(self) -> int:
        return 0

    async def sync_activities(self) -> int:
        return 0

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        return {}

    async def query(self, request: str) -> dict[str, Any]:
        req: str = (request or "").strip()
        if not req.lower().startswith("read "):
            return {
                "error": "Artifact query must use format 'read <artifact_id>'. Pass the UUID of the artifact to read."
            }
        artifact_id_raw: str = req[5:].strip()
        if not artifact_id_raw:
            return {"error": "artifact_id is required after 'read '"}
        try:
            artifact_uuid: UUID = UUID(artifact_id_raw)
        except ValueError:
            return {"error": "Invalid artifact_id format (must be a valid UUID)"}

        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(Artifact).where(
                    Artifact.id == artifact_uuid,
                    Artifact.organization_id == UUID(self.organization_id),
                )
            )
            artifact: Artifact | None = result.scalar_one_or_none()
        if not artifact:
            return {"error": "Artifact not found or access denied"}

        return {
            "id": str(artifact.id),
            "title": artifact.title or "Untitled",
            "filename": artifact.filename or "artifact.txt",
            "content_type": artifact.content_type or "text",
            "content": artifact.content or "",
        }

    async def write(self, operation: str, data: dict[str, Any]) -> dict[str, Any]:
        if operation == "create":
            return await self._create(data)
        if operation == "update":
            return await self._update(data)
        return {"error": f"Unknown operation: {operation}. Use 'create' or 'update'."}

    _MIME_TO_SHORT: dict[str, str] = {
        "text/plain": "text",
        "text/markdown": "markdown",
        "text/x-markdown": "markdown",
        "application/pdf": "pdf",
        "application/json": "chart",
    }
    _VALID_TYPES: set[str] = {"text", "markdown", "pdf", "chart"}

    @classmethod
    def _normalise_content_type(cls, raw: str) -> str:
        return cls._MIME_TO_SHORT.get(raw, raw)

    async def _create(self, data: dict[str, Any]) -> dict[str, Any]:
        title: str = str(data.get("title", "Untitled"))
        filename: str = str(data.get("filename", "artifact.txt"))
        content_type: str = self._normalise_content_type(str(data.get("content_type", "text")))
        content: str = str(data.get("content", ""))
        conversation_id: str | None = data.get("conversation_id")
        message_id: str | None = data.get("message_id")

        if content_type not in self._VALID_TYPES:
            return {
                "error": f"Invalid content_type '{content_type}'. Must be one of: {', '.join(self._VALID_TYPES)}"
            }
        if not content.strip():
            return {"error": "Content cannot be empty"}
        if content_type == "chart":
            try:
                chart_spec: Any = json.loads(content)
                if not isinstance(chart_spec, dict) or "data" not in chart_spec:
                    return {"error": "Chart content must be a JSON object with a 'data' field"}
            except json.JSONDecodeError as e:
                return {"error": f"Invalid JSON for chart: {e}"}

        mime_type: str = CONTENT_TYPE_TO_MIME.get(content_type, "application/octet-stream")
        stored_content: str = content
        if content_type == "pdf":
            mime_type = "text/markdown"

        artifact_uuid: UUID = uuid4()
        artifact_id_str: str = str(artifact_uuid)
        user_uuid: UUID | None = UUID(self.user_id) if self.user_id else None

        async with get_session(organization_id=self.organization_id) as session:
            artifact = Artifact(
                id=artifact_uuid,
                user_id=user_uuid,
                organization_id=UUID(self.organization_id),
                type="file",
                title=title,
                content=stored_content,
                content_type=content_type,
                mime_type=mime_type,
                filename=filename,
                conversation_id=UUID(conversation_id) if conversation_id else None,
                message_id=UUID(message_id) if message_id else None,
            )
            session.add(artifact)
            await session.commit()

        logger.info(
            "[ArtifactConnector] Created artifact: id=%s, type=%s, title=%s",
            artifact_id_str,
            content_type,
            title,
        )
        view_url: str = f"{settings.FRONTEND_URL.rstrip('/')}/artifacts/{artifact_id_str}"
        return {
            "status": "success",
            "artifact_id": artifact_id_str,
            "artifact": {
                "id": artifact_id_str,
                "title": title,
                "filename": filename,
                "contentType": content_type,
                "mimeType": mime_type,
                "viewUrl": view_url,
            },
            "view_url": view_url,
            "message": f"Created {content_type} artifact: {title}",
        }

    async def _update(self, data: dict[str, Any]) -> dict[str, Any]:
        artifact_id_raw: str | None = data.get("artifact_id")
        if not artifact_id_raw or not str(artifact_id_raw).strip():
            return {"error": "artifact_id is required for update"}
        try:
            artifact_uuid: UUID = UUID(str(artifact_id_raw).strip())
        except ValueError:
            return {"error": "Invalid artifact_id format"}

        content: str | None = data.get("content")
        title: str | None = data.get("title")
        filename: str | None = data.get("filename")
        content_type: str | None = data.get("content_type")
        if not any(v is not None for v in (content, title, filename, content_type)):
            return {"error": "At least one of content, title, filename, or content_type must be provided"}

        if content_type is not None:
            content_type = self._normalise_content_type(content_type)
            if content_type not in self._VALID_TYPES:
                return {"error": f"Invalid content_type '{content_type}'"}
        if content is not None and not str(content).strip():
            return {"error": "Content cannot be empty"}

        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(Artifact).where(
                    Artifact.id == artifact_uuid,
                    Artifact.organization_id == UUID(self.organization_id),
                )
            )
            existing: Artifact | None = result.scalar_one_or_none()
        if not existing:
            return {"error": "Artifact not found or access denied"}

        updates: dict[str, Any] = {}
        if content is not None:
            updates["content"] = content
        if title is not None:
            updates["title"] = title
        if filename is not None:
            updates["filename"] = filename
        if content_type is not None:
            updates["content_type"] = content_type
            updates["mime_type"] = CONTENT_TYPE_TO_MIME.get(content_type, "application/octet-stream")

        if updates:
            async with get_session(organization_id=self.organization_id) as session:
                await session.execute(
                    update(Artifact).where(Artifact.id == artifact_uuid).values(**updates)
                )
                await session.commit()

        final_title: str = title if title is not None else (existing.title or "Untitled")
        final_filename: str = filename if filename is not None else (existing.filename or "artifact.txt")
        final_content_type: str = content_type if content_type is not None else (existing.content_type or "text")
        artifact_id_str: str = str(artifact_uuid)
        view_url: str = f"{settings.FRONTEND_URL.rstrip('/')}/artifacts/{artifact_id_str}"

        logger.info("[ArtifactConnector] Updated artifact: id=%s", artifact_id_str)
        return {
            "status": "success",
            "artifact_id": artifact_id_str,
            "artifact": {
                "id": artifact_id_str,
                "title": final_title,
                "filename": final_filename,
                "contentType": final_content_type,
                "mimeType": CONTENT_TYPE_TO_MIME.get(final_content_type, "application/octet-stream"),
                "viewUrl": view_url,
                "updated": True,
            },
            "view_url": view_url,
            "message": f"Updated artifact: {final_title}",
        }

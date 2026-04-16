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
from models.chat_message import ChatMessage
from models.conversation import Conversation
from models.external_identity_mapping import ExternalIdentityMapping
from connectors.registry import (
    AuthType,
    Capability,
    ConnectorMeta,
    ConnectorScope,
    WriteOperation,
)
from models.artifact import Artifact
from models.database import get_session
from models.organization import Organization
from services.public_preview_warmup import warm_public_preview_cache
from services.slack_identity import get_alternate_slack_user_ids_for_identity

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

        artifact_payload: dict[str, str] | None = None
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(Artifact).where(
                    Artifact.id == artifact_uuid,
                    Artifact.organization_id == UUID(self.organization_id),
                )
            )
            artifact: Artifact | None = result.scalar_one_or_none()
            if artifact:
                # Copy values while the row is still session-bound.
                # get_session() performs a rollback in cleanup, which expires ORM
                # attributes; reading after context exit can raise DetachedInstanceError.
                artifact_payload = {
                    "id": str(artifact.id),
                    "title": artifact.title or "Untitled",
                    "filename": artifact.filename or "artifact.txt",
                    "content_type": artifact.content_type or "text",
                    "content": artifact.content or "",
                }

        if not artifact_payload:
            return {"error": "Artifact not found or access denied"}
        return artifact_payload

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

    async def _resolve_user_from_external_actor(
        self,
        *,
        source: str | None,
        external_user_id: str | None,
    ) -> UUID | None:
        """Resolve an internal user from an external actor identifier."""
        normalized_source: str = (source or "").strip().lower()
        normalized_external_user: str = (external_user_id or "").strip().upper()
        if not normalized_source or not normalized_external_user:
            logger.debug(
                "[ArtifactConnector] External actor resolution skipped due to missing source/external_user_id: source=%s external_user_id=%s",
                source,
                external_user_id,
            )
            return None

        if normalized_source != "slack":
            logger.debug(
                "[ArtifactConnector] External actor resolution skipped for unsupported source: source=%s external_user_id=%s",
                normalized_source,
                normalized_external_user,
            )
            return None

        org_uuid: UUID = UUID(self.organization_id)
        candidate_external_user_ids: list[str] = [normalized_external_user]
        async with get_session(organization_id=self.organization_id) as session:
            alternate_slack_ids: list[str] = await get_alternate_slack_user_ids_for_identity(
                organization_id=self.organization_id,
                slack_user_id=normalized_external_user,
                session=session,
            )
            for alternate_slack_id in alternate_slack_ids:
                normalized_alternate: str = str(alternate_slack_id).strip().upper()
                if normalized_alternate and normalized_alternate not in candidate_external_user_ids:
                    candidate_external_user_ids.append(normalized_alternate)

            logger.info(
                "[ArtifactConnector] Attempting external actor owner resolution across Slack identities: source=%s external_user_ids=%s",
                normalized_source,
                candidate_external_user_ids,
            )

            mapping_rows = await session.execute(
                select(
                    ExternalIdentityMapping.external_userid,
                    ExternalIdentityMapping.source,
                    ExternalIdentityMapping.user_id,
                )
                .where(ExternalIdentityMapping.organization_id == org_uuid)
                .where(ExternalIdentityMapping.external_userid.in_(candidate_external_user_ids))
                .where(ExternalIdentityMapping.source.in_(("slack", "revtops_unknown")))
                .where(ExternalIdentityMapping.user_id.is_not(None))
                .order_by(ExternalIdentityMapping.updated_at.desc())
            )
            mappings: list[tuple[str, str, UUID]] = list(mapping_rows.all())
            if mappings:
                selected_external_user_id: str
                selected_source: str
                selected_user_id: UUID
                selected_external_user_id, selected_source, selected_user_id = mappings[0]
                logger.info(
                    "[ArtifactConnector] Resolved artifact owner from Slack identity candidates: selected_external_user_id=%s source=%s user_id=%s total_candidate_mappings=%d",
                    selected_external_user_id,
                    selected_source,
                    selected_user_id,
                    len(mappings),
                )
                return selected_user_id

        logger.debug(
            "[ArtifactConnector] Could not resolve artifact owner from external actor: source=%s external_user_id=%s",
            normalized_source,
            normalized_external_user,
        )
        return None

    async def _create(self, data: dict[str, Any]) -> dict[str, Any]:
        title: str = str(data.get("title", "Untitled"))
        filename: str = str(data.get("filename", "artifact.txt"))
        content_type: str = self._normalise_content_type(str(data.get("content_type", "text")))
        content: str = str(data.get("content", ""))
        conversation_id: str | None = data.get("conversation_id")
        message_id: str | None = data.get("message_id")

        logger.info(
            "[ArtifactConnector] Creating artifact with ownership context: org_id=%s message_id=%s conversation_id=%s connector_user_id=%s",
            self.organization_id,
            message_id,
            conversation_id,
            self.user_id,
        )

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
        user_uuid: UUID | None = None
        if message_id:
            try:
                message_uuid = UUID(message_id)
            except (ValueError, TypeError, AttributeError):
                logger.warning(
                    "[ArtifactConnector] Could not parse message_id as UUID for owner resolution: message_id=%s",
                    message_id,
                )
            else:
                async with get_session(organization_id=self.organization_id) as session:
                    row = await session.execute(
                        select(ChatMessage.user_id).where(
                            ChatMessage.id == message_uuid,
                        )
                    )
                    message_user_id: UUID | None = row.scalar_one_or_none()
                    if message_user_id is not None:
                        user_uuid = message_user_id
                        logger.info(
                            "[ArtifactConnector] Resolved artifact owner from initiating message: message_id=%s user_id=%s",
                            message_id,
                            message_user_id,
                        )

        connector_user_uuid: UUID | None = None
        if self.user_id:
            try:
                connector_user_uuid = UUID(self.user_id)
            except (ValueError, TypeError, AttributeError):
                logger.warning(
                    "[ArtifactConnector] Could not parse connector user_id as UUID for owner resolution: user_id=%s",
                    self.user_id,
                )

        if not user_uuid and conversation_id:
            try:
                conversation_uuid: UUID = UUID(conversation_id)
            except (ValueError, TypeError, AttributeError):
                logger.warning(
                    "[ArtifactConnector] Could not parse conversation_id as UUID for owner resolution fallback: conversation_id=%s",
                    conversation_id,
                )
            else:
                async with get_session(organization_id=self.organization_id) as session:
                    row = await session.execute(
                        select(
                            Conversation.user_id,
                            Conversation.source,
                            Conversation.source_user_id,
                        ).where(
                            Conversation.id == conversation_uuid,
                        )
                    )
                    conversation_record: tuple[UUID | None, str | None, str | None] | None = row.one_or_none()
                    conversation_user_id: UUID | None = None
                    conversation_source: str | None = None
                    conversation_source_user_id: str | None = None
                    if conversation_record is not None:
                        (
                            conversation_user_id,
                            conversation_source,
                            conversation_source_user_id,
                        ) = conversation_record
                    if conversation_user_id is not None:
                        user_uuid = conversation_user_id
                        logger.info(
                            "[ArtifactConnector] Falling back to conversation owner for artifact owner: conversation_id=%s user_id=%s",
                            conversation_id,
                            conversation_user_id,
                        )
                    else:
                        external_actor_user_id: UUID | None = await self._resolve_user_from_external_actor(
                            source=conversation_source,
                            external_user_id=conversation_source_user_id,
                        )
                        if external_actor_user_id is not None:
                            user_uuid = external_actor_user_id
                            logger.info(
                                "[ArtifactConnector] Resolved artifact owner from external actor mapping: conversation_id=%s source=%s external_user_id=%s user_id=%s",
                                conversation_id,
                                conversation_source,
                                conversation_source_user_id,
                                external_actor_user_id,
                            )

        if user_uuid is None and connector_user_uuid is not None:
            user_uuid = connector_user_uuid
            logger.info(
                "[ArtifactConnector] Falling back to connector user context for artifact owner: user_id=%s",
                connector_user_uuid,
            )

        if user_uuid is None:
            logger.warning(
                "[ArtifactConnector] Artifact owner unresolved; creating ownerless artifact: org_id=%s message_id=%s conversation_id=%s",
                self.organization_id,
                message_id,
                conversation_id,
            )

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
        await warm_public_preview_cache("artifact", artifact_id_str)
        artifact_uri_path: str = await self._build_artifact_uri_path(artifact_id_str)
        view_url: str = f"{settings.FRONTEND_URL.rstrip('/')}{artifact_uri_path}"
        return {
            "status": "success",
            "artifact_id": artifact_id_str,
            "artifact": {
                "id": artifact_id_str,
                "title": title,
                "filename": filename,
                "contentType": content_type,
                "mimeType": mime_type,
                "uri": artifact_uri_path,
                "viewUrl": view_url,
            },
            "uri": artifact_uri_path,
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
            prev_title: str = existing.title or "Untitled"
            prev_filename: str = existing.filename or "artifact.txt"
            prev_content_type: str = existing.content_type or "text"

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

        final_title: str = title if title is not None else prev_title
        final_filename: str = filename if filename is not None else prev_filename
        final_content_type: str = content_type if content_type is not None else prev_content_type
        artifact_id_str: str = str(artifact_uuid)
        artifact_uri_path: str = await self._build_artifact_uri_path(artifact_id_str)
        view_url: str = f"{settings.FRONTEND_URL.rstrip('/')}{artifact_uri_path}"

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
                "uri": artifact_uri_path,
                "viewUrl": view_url,
                "updated": True,
            },
            "uri": artifact_uri_path,
            "view_url": view_url,
            "message": f"Updated artifact: {final_title}",
        }

    async def _build_artifact_uri_path(self, artifact_id: str) -> str:
        org_handle: str | None = await self._get_org_handle()
        if org_handle:
            return f"/{org_handle}/artifacts/{artifact_id}"
        return f"/artifacts/{artifact_id}"

    async def _get_org_handle(self) -> str | None:
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(Organization.handle).where(Organization.id == UUID(self.organization_id))
            )
            org_handle: str | None = result.scalar_one_or_none()
        normalized: str = (org_handle or "").strip()
        return normalized or None

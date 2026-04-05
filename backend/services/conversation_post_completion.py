"""
Unified post-completion: summary + embedding in one process.

Runs after every assistant reply (WebSocket, REST, Slack, workflows).
Single entry point so both summary and embedding stay in sync.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from models.database import get_session
from models.workstream_snapshot import WorkstreamSnapshot
from sqlalchemy import update

logger = logging.getLogger(__name__)


async def _mark_workstream_snapshots_stale(organization_id: str) -> None:
    """Set stale_since on all workstream snapshots for this org."""
    try:
        async with get_session(organization_id=organization_id) as session:
            await session.execute(
                update(WorkstreamSnapshot)
                .where(WorkstreamSnapshot.organization_id == UUID(organization_id))
                .values(stale_since=datetime.now(timezone.utc))
            )
            await session.commit()
    except Exception:
        logger.debug(
            "Could not mark workstream snapshots stale for org %s",
            organization_id,
            exc_info=True,
        )


async def run_post_completion(conversation_id: str, organization_id: str) -> None:
    """
    Generate/refresh summary and embedding, then broadcast as needed.

    Called once per assistant reply from the orchestrator. Non-blocking;
    errors are logged and not raised.
    """
    try:
        from api.websockets import sync_broadcaster
        from services.conversation_embeddings import update_conversation_embedding
        from services.conversation_summary import (
            generate_conversation_summary,
            generate_conversation_title,
        )

        summary_text: str | None = await generate_conversation_summary(
            conversation_id, organization_id
        )
        if summary_text:
            await sync_broadcaster.broadcast(
                organization_id,
                "summary_updated",
                {"conversation_id": conversation_id, "summary": summary_text},
            )

        new_title: str | None = await generate_conversation_title(
            conversation_id, organization_id
        )
        if new_title:
            await sync_broadcaster.broadcast(
                organization_id,
                "title_updated",
                {"conversation_id": conversation_id, "title": new_title},
            )

        embedding_updated = await update_conversation_embedding(
            conversation_id, organization_id
        )
        if embedding_updated:
            await _mark_workstream_snapshots_stale(organization_id)
            await sync_broadcaster.broadcast(organization_id, "workstreams_stale", {})
    except Exception:
        logger.warning(
            "Post-completion failed for conversation %s",
            conversation_id,
            exc_info=True,
        )

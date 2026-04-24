from __future__ import annotations

import logging
from typing import Any

from services.campfires import list_campfire_context_summaries
from services.content_groups import list_recent_summaries

logger = logging.getLogger(__name__)


async def build_incoming_message_context_pack(
    *,
    organization_id: str,
    content_group_id: str | None,
    summary_limit: int = 3,
    campfire_limit: int = 2,
    token_budget_chars: int = 3500,
) -> dict[str, Any] | None:
    if not content_group_id:
        return None

    source_summaries = await list_recent_summaries(
        content_group_id=content_group_id,
        limit=summary_limit,
        max_age_hours=72,
        organization_id=organization_id,
    )
    campfire_summaries = await list_campfire_context_summaries(
        organization_id=organization_id,
        content_group_id=content_group_id,
        summary_limit_per_group=campfire_limit,
    )

    blocks: list[str] = []
    pointers: list[dict[str, Any]] = []
    for label, summaries in (
        ("Recent context from source channel/chat", source_summaries),
        ("Relevant campfire context", campfire_summaries),
    ):
        if not summaries:
            continue
        parts = [label + ":"]
        for s in summaries:
            parts.append(
                f"- {s.summary_text}\n"
                f"  range={s.first_message_at.isoformat()}..{s.last_message_at.isoformat()}"
                f" ids={s.first_message_external_id}->{s.last_message_external_id}"
                f" summarized_at={s.summarized_through_at.isoformat()}"
            )
            pointers.append({
                "summary_id": str(s.id),
                "content_group_id": str(s.content_group_id),
                "summarized_through_at": s.summarized_through_at.isoformat(),
            })
        blocks.append("\n".join(parts))

    if not blocks:
        return None

    raw_text = "\n\n".join(blocks)
    trimmed_text = raw_text[:token_budget_chars]
    selected_count = sum(1 for _ in pointers)
    logger.info(
        "[context_pack.build] org_id=%s content_group_id=%s incoming_context_pack_summaries_count=%d chars=%d",
        organization_id,
        content_group_id,
        selected_count,
        len(trimmed_text),
    )

    return {
        "context_text": trimmed_text,
        "summary_pointers": pointers,
        "incoming_context_pack_summaries_count": selected_count,
    }

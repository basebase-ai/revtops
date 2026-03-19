#!/usr/bin/env python3
"""
One-time backfill: generate embeddings for conversations (for workstream clustering).

By default only processes conversations that already have a summary. Use
--include-no-summary to also embed conversations without a summary (uses
title + recent messages only).

Usage:
    cd backend && python scripts/backfill_conversation_embeddings.py [--limit N] [--org ORG_ID] [--include-no-summary]

Requires OPENAI_API_KEY in env. Uses get_session per org for RLS.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from models.conversation import Conversation
from models.chat_message import ChatMessage as ChatMessageModel
from models.database import get_admin_session, get_session
from services.conversation_embeddings import (
    _MAX_RECENT_CHARS,
    _MAX_MESSAGES_FOR_RECENT,
    build_embedding_text,
)
from services.embeddings import get_embedding_service


def _extract_text_from_blocks(blocks: list | None) -> str:
    if not blocks:
        return ""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text")
            if text:
                parts.append(str(text))
        elif block.get("type") == "tool_use":
            name = block.get("name")
            if name:
                parts.append(f"[{name}]")
    return " ".join(parts)


async def backfill(
    limit: int | None,
    org_id: str | None,
    include_no_summary: bool,
) -> None:
    if org_id:
        org_ids: list[str] = [org_id]
    else:
        async with get_admin_session() as session:
            q = select(Conversation.organization_id).where(
                Conversation.embedding.is_(None),
                Conversation.organization_id.isnot(None),
            )
            if not include_no_summary:
                q = q.where(Conversation.summary.isnot(None))
            result = await session.execute(q.distinct())
            org_ids = [str(r[0]) for r in result.all() if r[0]]

    if not org_ids:
        print("No organizations with conversations needing embeddings.")
        return

    embedding_service = get_embedding_service()
    batch_size = 50
    total_processed = 0

    for oid in org_ids:
        async with get_session(organization_id=oid) as session:
            q = (
                select(Conversation)
                .where(
                    Conversation.organization_id == oid,
                    Conversation.embedding.is_(None),
                )
                .order_by(Conversation.updated_at.desc())
            )
            if not include_no_summary:
                q = q.where(Conversation.summary.isnot(None))
            result = await session.execute(q)
            convs: list[Conversation] = list(result.scalars().all())

        if limit is not None and total_processed >= limit:
            break
        if limit is not None:
            convs = convs[: limit - total_processed]

        if not convs:
            continue

        texts: list[str] = []
        valid_convs: list[Conversation] = []

        for conv in convs:
            summary_overall: str | None = None
            if conv.summary:
                try:
                    parsed = json.loads(conv.summary)
                    summary_overall = parsed.get("overall") if isinstance(parsed, dict) else None
                except (json.JSONDecodeError, TypeError):
                    pass

            async with get_session(organization_id=oid) as session:
                result = await session.execute(
                    select(ChatMessageModel)
                    .where(
                        ChatMessageModel.conversation_id == conv.id,
                        ChatMessageModel.role == "user",
                    )
                    .order_by(ChatMessageModel.created_at.desc())
                    .limit(_MAX_MESSAGES_FOR_RECENT)
                )
                user_messages = list(result.scalars().all())

            recent_texts: list[str] = []
            total_chars = 0
            for msg in reversed(user_messages):
                text = _extract_text_from_blocks(msg.content_blocks)
                if not text and getattr(msg, "content", None):
                    text = msg.content or ""
                if text:
                    recent_texts.append(text)
                    total_chars += len(text)
                    if total_chars >= _MAX_RECENT_CHARS:
                        break

            emb_text = build_embedding_text(
                title=conv.title,
                summary_overall=summary_overall,
                recent_user_texts=recent_texts,
            )
            if not emb_text or emb_text == "Untitled conversation":
                emb_text = f"Conversation {conv.id}"
            texts.append(emb_text)
            valid_convs.append(conv)

        for i in range(0, len(valid_convs), batch_size):
            batch_convs = valid_convs[i : i + batch_size]
            batch_texts = texts[i : i + batch_size]
            vectors = await embedding_service.generate_embeddings_batch(batch_texts)

            async with get_session(organization_id=oid) as session:
                for c, vec in zip(batch_convs, vectors):
                    conv = await session.get(Conversation, c.id)
                    if conv and conv.embedding.is_(None):
                        conv.embedding = vec
                        conv.embedding_message_count = conv.message_count
                        total_processed += 1
                await session.commit()

        print(f"Org {oid}: processed {min(len(convs), len(valid_convs))} conversations (total so far: {total_processed})")

    print(f"\nDone. Total conversations embedded: {total_processed}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill conversation embeddings")
    parser.add_argument("--limit", type=int, default=None, help="Max conversations to process")
    parser.add_argument("--org", type=str, default=None, help="Only this organization ID")
    parser.add_argument(
        "--include-no-summary",
        action="store_true",
        help="Also embed conversations that have no summary (uses title + recent messages)",
    )
    args = parser.parse_args()
    asyncio.run(
        backfill(
            limit=args.limit,
            org_id=args.org,
            include_no_summary=args.include_no_summary,
        )
    )


if __name__ == "__main__":
    main()

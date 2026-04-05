#!/usr/bin/env python3
"""
Backfill: generate LLM summaries and titles for recent conversations.

Iterates across all orgs, finds agent conversations updated within --since-days,
and runs the same summary + title generators used in post-completion.
Conversations that already have a summary / upgraded title are skipped
automatically by the generators' internal checks.

Usage:
    cd backend && python scripts/backfill_conversation_summaries.py [--since-days 14] [--limit 500] [--delay 0.35] [--org ORG_ID]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, desc

from models.conversation import Conversation
from models.database import get_admin_session
from services.conversation_summary import (
    generate_conversation_summary,
    generate_conversation_title,
)


async def backfill(
    since_days: int,
    limit: int,
    delay: float,
    org_id: str | None,
) -> None:
    cutoff: datetime = datetime.utcnow() - timedelta(days=since_days)

    async with get_admin_session() as session:
        q = (
            select(Conversation.id, Conversation.organization_id)
            .where(
                Conversation.type == "agent",
                Conversation.organization_id.isnot(None),
                Conversation.updated_at >= cutoff,
            )
            .order_by(desc(Conversation.updated_at))
            .limit(limit)
        )
        if org_id:
            q = q.where(Conversation.organization_id == org_id)
        rows = (await session.execute(q)).all()

    pairs: list[tuple[str, str]] = [
        (str(r.id), str(r.organization_id)) for r in rows if r.organization_id
    ]
    total: int = len(pairs)
    print(f"Found {total} agent conversations updated in the last {since_days} days.")

    summaries_done: int = 0
    titles_done: int = 0

    for i, (conv_id, oid) in enumerate(pairs, 1):
        s: str | None = await generate_conversation_summary(conv_id, oid)
        if s:
            summaries_done += 1
        t: str | None = await generate_conversation_title(conv_id, oid)
        if t:
            titles_done += 1

        label: str = "." if not s and not t else f" [S{'✓' if s else '·'}T{'✓' if t else '·'}]"
        if i % 10 == 0 or i == total:
            print(f"  {i}/{total}{label}  (summaries: {summaries_done}, titles: {titles_done})")

        if delay > 0 and i < total:
            await asyncio.sleep(delay)

    print(f"\nDone. Processed: {total}, summaries generated: {summaries_done}, titles generated: {titles_done}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill conversation summaries and titles")
    parser.add_argument("--since-days", type=int, default=14, help="Only conversations updated within this many days (default: 14)")
    parser.add_argument("--limit", type=int, default=500, help="Max conversations to process (default: 500)")
    parser.add_argument("--delay", type=float, default=0.35, help="Seconds between API calls (default: 0.35)")
    parser.add_argument("--org", type=str, default=None, help="Only this organization ID")
    args = parser.parse_args()
    asyncio.run(backfill(since_days=args.since_days, limit=args.limit, delay=args.delay, org_id=args.org))


if __name__ == "__main__":
    main()

"""
One-off script to backfill Gemini meeting summaries from Google Drive
for completed meetings that have a meet_space_name but no summary yet.

Usage:
    cd backend
    railway run -- bash -c 'export REDIS_URL=redis://localhost:6379 && venv/bin/python scripts/backfill_gemini_summaries.py'
"""
import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from sqlalchemy import select

from models.database import get_admin_session, get_session
from models.meeting import Meeting
from workers.tasks.sync import _fetch_gemini_summary, _get_google_token


async def backfill():
    # Find all completed meetings with no summary that have a title or meet_space_name
    from sqlalchemy import or_
    async with get_admin_session() as session:
        result = await session.execute(
            select(Meeting).where(
                Meeting.status == "completed",
                or_(
                    Meeting.meet_space_name.isnot(None),
                    Meeting.meeting_code.isnot(None),
                    Meeting.title.isnot(None),
                ),
                Meeting.missing_notes_filter("gemini"),
            ).order_by(Meeting.scheduled_start.desc())
        )
        meetings = result.scalars().all()

    if not meetings:
        print("No meetings to backfill.")
        return

    print(f"Found {len(meetings)} meeting(s) to backfill:\n")
    for m in meetings:
        print(f"  {m.id}  '{m.title}'  started={m.scheduled_start}  organizer={m.organizer_email}")

    print()
    filled = 0
    skipped = 0

    for meeting in meetings:
        org_id = str(meeting.organization_id)
        meeting_id = str(meeting.id)
        title = meeting.title or "Huddle"
        start_time = meeting.scheduled_start

        async with httpx.AsyncClient() as client:
            summary, doc_id = await _fetch_gemini_summary(client, org_id, meeting.organizer_email, title, start_time, meeting_id)

        if summary:
            async with get_session(organization_id=org_id) as session:
                m = await session.get(Meeting, meeting.id)
                m.set_notes("gemini", summary, doc_id=doc_id)
                await session.commit()
            print(f"  OK   {meeting_id}: saved {len(summary)} chars (doc={doc_id})")
            filled += 1
        else:
            print(f"  MISS {meeting_id}: no summary doc found in Drive")
            skipped += 1

    print(f"\nDone. Filled: {filled}, Skipped: {skipped}")


if __name__ == "__main__":
    asyncio.run(backfill())

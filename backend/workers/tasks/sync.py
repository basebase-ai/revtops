"""
Sync tasks for Celery workers.

These tasks handle syncing data from external integrations (CRM, calendar, etc.)
on a scheduled or on-demand basis.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend directory is in Python path for Celery forked workers
_backend_dir = Path(__file__).resolve().parent.parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Provider-specific sync cadence for periodic global sync runs.
# Default cadence remains hourly unless explicitly overridden.
PROVIDER_SYNC_INTERVALS: dict[str, timedelta] = {
    "google_drive": timedelta(minutes=30),
}
DEFAULT_SYNC_INTERVAL: timedelta = timedelta(hours=1)


_worker_loop: asyncio.AbstractEventLoop | None = None


def run_async(coro: Any) -> Any:
    """Run an async function in a sync context (for Celery tasks).

    Reuses a single event loop per worker process so that asyncpg connections
    remain valid across task invocations.
    """
    global _worker_loop

    if _worker_loop is None or _worker_loop.is_closed():
        from models.database import dispose_engine
        dispose_engine()
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)

    return _worker_loop.run_until_complete(coro)


async def _sync_integration(
    organization_id: str,
    provider: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """
    Internal async function to sync a single integration.

    Args:
        organization_id: UUID of the organization.
        provider: Integration provider name.
        user_id: Optional UUID of the user who owns this integration
                 (for per-user providers like Gmail, Calendar, etc.).

    Returns sync results including counts and any errors.
    """
    from connectors.base import SyncCancelledError
    from connectors.registry import discover_connectors
    from services.embedding_sync import generate_embeddings_for_organization
    from workers.events import emit_event

    connectors = discover_connectors()

    connector_class = connectors.get(provider)
    if not connector_class:
        return {
            "status": "failed",
            "error": f"Unknown provider: {provider}",
            "organization_id": organization_id,
            "provider": provider,
        }

    try:
        user_label: str = f" user={user_id}" if user_id else ""
        logger.info(f"Starting sync for {provider} in org {organization_id}{user_label}")
        connector = connector_class(organization_id, user_id=user_id)

        from access_control import ConnectorContext, check_connector_call

        dp_ctx = ConnectorContext(
            organization_id=organization_id,
            user_id=user_id,
            provider=provider,
            operation="sync",
        )
        dp_result = await check_connector_call(dp_ctx, None)
        if not dp_result.allowed:
            return {
                "status": "failed",
                "organization_id": organization_id,
                "provider": provider,
                "error": dp_result.deny_reason or "Connector sync not allowed",
            }

        counts = await connector.sync_all()
        await connector.update_last_sync(counts)

        # Generate embeddings for newly synced activities
        try:
            embedded_count = await generate_embeddings_for_organization(
                organization_id, limit=500
            )
            logger.info(f"Generated embeddings for {embedded_count} activities")
        except Exception as embed_err:
            logger.warning(f"Embedding generation failed: {embed_err}")

        # Emit sync completed event for workflow triggers
        await emit_event(
            event_type="sync.completed",
            organization_id=organization_id,
            data={
                "provider": provider,
                "counts": counts,
                "completed_at": datetime.utcnow().isoformat(),
            },
        )

        logger.info(f"Completed sync for {provider} in org {organization_id}: {counts}")
        return {
            "status": "completed",
            "organization_id": organization_id,
            "provider": provider,
            "counts": counts,
        }

    except SyncCancelledError as e:
        cancel_msg = str(e)
        logger.info(f"Sync cancelled for {provider} in org {organization_id}: {cancel_msg}")
        return {
            "status": "cancelled",
            "organization_id": organization_id,
            "provider": provider,
            "error": cancel_msg,
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Sync failed for {provider} in org {organization_id}: {error_msg}")

        # Record error in database
        try:
            connector = connector_class(organization_id, user_id=user_id)
            await connector.record_error(error_msg)
        except Exception:
            pass

        # Emit sync failed event
        await emit_event(
            event_type="sync.failed",
            organization_id=organization_id,
            data={
                "provider": provider,
                "error": error_msg,
                "failed_at": datetime.utcnow().isoformat(),
            },
        )

        return {
            "status": "failed",
            "organization_id": organization_id,
            "provider": provider,
            "error": error_msg,
        }


async def _get_all_active_integrations() -> list[dict[str, str | None]]:
    """Get all active integrations across all organizations."""
    from sqlalchemy import select
    from models.database import get_admin_session
    from models.integration import Integration

    # Use admin session to bypass RLS and query across all organizations
    async with get_admin_session() as session:
        result = await session.execute(
            select(Integration).where(Integration.is_active == True)
        )
        integrations = result.scalars().all()
        
        return [
            {
                "organization_id": str(i.organization_id),
                "connector": i.connector,
                "user_id": str(i.user_id) if i.user_id else None,
                "last_sync_at": i.last_sync_at.isoformat() if i.last_sync_at else None,
            }
            for i in integrations
        ]


def _should_sync_in_periodic_run(integration: dict[str, str | None], now: datetime) -> bool:
    """Return whether an integration is due for sync in the periodic global run."""
    provider: str = integration["connector"]  # type: ignore[assignment]
    cadence: timedelta = PROVIDER_SYNC_INTERVALS.get(provider, DEFAULT_SYNC_INTERVAL)
    raw_last_sync_at: str | None = integration.get("last_sync_at")

    if not raw_last_sync_at:
        logger.info(
            "Periodic sync due provider=%s org=%s user=%s reason=never_synced",
            provider,
            integration.get("organization_id"),
            integration.get("user_id"),
        )
        return True

    try:
        last_sync_at = datetime.fromisoformat(raw_last_sync_at)
    except ValueError:
        logger.warning(
            "Periodic sync forced provider=%s org=%s user=%s reason=invalid_last_sync_at value=%s",
            provider,
            integration.get("organization_id"),
            integration.get("user_id"),
            raw_last_sync_at,
        )
        return True

    elapsed: timedelta = now - last_sync_at
    due: bool = elapsed >= cadence
    if not due:
        logger.info(
            "Skipping periodic sync provider=%s org=%s user=%s elapsed_seconds=%s min_interval_seconds=%s",
            provider,
            integration.get("organization_id"),
            integration.get("user_id"),
            int(elapsed.total_seconds()),
            int(cadence.total_seconds()),
        )
    return due


async def _get_org_integrations(organization_id: str) -> list[dict[str, str | None]]:
    """Get all active integrations for an organization (including per-user)."""
    from sqlalchemy import select
    from models.database import get_session
    from models.integration import Integration

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.is_active == True,
            )
        )
        integrations = result.scalars().all()
        return [
            {
                "connector": i.connector,
                "user_id": str(i.user_id) if i.user_id else None,
            }
            for i in integrations
        ]


@celery_app.task(bind=True, name="workers.tasks.sync.sync_integration")
def sync_integration(
    self: Any,
    organization_id: str,
    provider: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    """
    Celery task to sync a single integration.
    
    Args:
        organization_id: UUID of the organization
        provider: Integration provider name (e.g., 'hubspot', 'salesforce')
        user_id: Optional UUID of the user who owns this integration
    
    Returns:
        Dict with sync status, counts, and any errors
    """
    user_label: str = f" user={user_id}" if user_id else ""
    logger.info(f"Task {self.request.id}: Syncing {provider} for org {organization_id}{user_label}")
    return run_async(_sync_integration(organization_id, provider, user_id=user_id))


@celery_app.task(bind=True, name="workers.tasks.sync.sync_organization")
def sync_organization(self: Any, organization_id: str) -> dict[str, Any]:
    """
    Celery task to sync all integrations for a single organization.
    
    Args:
        organization_id: UUID of the organization
    
    Returns:
        Dict with results for each integration
    """
    logger.info(f"Task {self.request.id}: Syncing all integrations for org {organization_id}")
    
    async def _sync_all_for_org() -> dict[str, Any]:
        integration_entries: list[dict[str, str | None]] = await _get_org_integrations(organization_id)
        results: dict[str, Any] = {}
        
        for entry in integration_entries:
            provider: str = entry["connector"]  # type: ignore[assignment]
            uid: str | None = entry["user_id"]
            key: str = f"{provider}:{uid}" if uid else provider
            results[key] = await _sync_integration(organization_id, provider, user_id=uid)
        
        return {
            "organization_id": organization_id,
            "integrations": results,
            "completed_at": datetime.utcnow().isoformat(),
        }
    
    return run_async(_sync_all_for_org())


@celery_app.task(
    bind=True,
    name="workers.tasks.sync.check_huddle_recording",
    max_retries=3,
    default_retry_delay=600,
)
def check_huddle_recording(
    self: Any,
    meeting_id: str,
    organization_id: str,
) -> dict[str, Any]:
    """
    Check Google Drive for a recording after a huddle ends.

    Retries up to 3 times with 10-minute delay to allow time for
    Google to process and upload the recording to Drive.
    """
    logger.info(f"Task {self.request.id}: Checking recording for meeting {meeting_id}")
    return run_async(_check_huddle_recording(self, meeting_id, organization_id))


async def _check_huddle_recording(
    task: Any,
    meeting_id: str,
    organization_id: str,
) -> dict[str, Any]:
    """Async implementation of huddle recording check.

    New path (meet_space_name set): uses Meet REST API v2 conference records
    to fetch participants, recordings, and transcripts.

    Legacy path (meet_space_name null, google_event_id set): falls back to
    fuzzy Drive search for video files.
    """
    from models.database import get_session
    from models.meeting import Meeting
    from workers.events import emit_event

    async with get_session(organization_id=organization_id) as session:
        meeting = await session.get(Meeting, UUID(meeting_id))
        if not meeting:
            return {"status": "skipped", "reason": "meeting_not_found"}

        if meeting.recording_drive_id:
            return {"status": "skipped", "reason": "recording_already_linked"}

        meet_space_name = meeting.meet_space_name
        title = meeting.title or "Huddle"
        start_time = meeting.scheduled_start
        organizer_email = meeting.organizer_email

    # ── Get an OAuth token (Calendar integration — same token works for Meet & Drive) ──
    token = await _get_google_token(task, organization_id, organizer_email)
    if token is None:
        return {"status": "skipped", "reason": "no_google_integration"}

    import httpx

    MEET_API = "https://meet.googleapis.com/v2"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # ── New path: Meet API conference records ──
    if meet_space_name:
        try:
            async with httpx.AsyncClient() as client:
                # Find the conference record for this space
                resp = await client.get(
                    f"{MEET_API}/conferenceRecords",
                    headers=headers,
                    params={"filter": f'space.name="{meet_space_name}"'},
                    timeout=30.0,
                )
                resp.raise_for_status()
                records = resp.json().get("conferenceRecords", [])

            if not records:
                remaining = task.max_retries - task.request.retries
                if remaining > 0:
                    logger.info(
                        "No conference record yet for meeting %s, %d retries remaining",
                        meeting_id, remaining,
                    )
                    raise task.retry()
                return {"status": "not_found", "meeting_id": meeting_id}

            # Use the most recent conference record
            conf_record = records[-1]
            conf_record_name = conf_record["name"]  # "conferenceRecords/abc123"

            recording_url = ""
            recording_drive_id = ""
            transcript_url = ""
            gemini_summary = ""
            participant_data: list[dict[str, Any]] = []

            async with httpx.AsyncClient() as client:
                # Fetch recordings
                rec_resp = await client.get(
                    f"{MEET_API}/{conf_record_name}/recordings",
                    headers=headers,
                    timeout=30.0,
                )
                if rec_resp.status_code == 200:
                    rec_json = rec_resp.json()
                    recordings = rec_json.get("recordings", [])
                    logger.info("Recordings response for %s: %s", meeting_id, rec_json)
                    if recordings:
                        drive_dest = recordings[0].get("driveDestination", {})
                        recording_url = drive_dest.get("exportUri", "")
                        recording_drive_id = drive_dest.get("file", "").split("/")[-1] if drive_dest.get("file") else ""
                else:
                    logger.warning("Recordings API returned %d for %s", rec_resp.status_code, meeting_id)

                # Fetch transcripts
                trans_resp = await client.get(
                    f"{MEET_API}/{conf_record_name}/transcripts",
                    headers=headers,
                    timeout=30.0,
                )
                if trans_resp.status_code == 200:
                    trans_json = trans_resp.json()
                    transcripts = trans_json.get("transcripts", [])
                    logger.info("Transcripts response for %s: %s", meeting_id, trans_json)
                    if transcripts:
                        docs_dest = transcripts[0].get("docsDestination", {})
                        transcript_url = docs_dest.get("exportUri", "")
                else:
                    logger.warning("Transcripts API returned %d for %s", trans_resp.status_code, meeting_id)

                # Fetch participants
                part_resp = await client.get(
                    f"{MEET_API}/{conf_record_name}/participants",
                    headers=headers,
                    timeout=30.0,
                )
                if part_resp.status_code == 200:
                    for p in part_resp.json().get("participants", []):
                        signin = p.get("signedinUser", {})
                        anon = p.get("anonymousUser", {})
                        participant_data.append({
                            "email": signin.get("user", ""),
                            "name": signin.get("displayName", anon.get("displayName", "")),
                        })

                # Fetch Gemini meeting summary doc from "Meet Recordings" in Drive
                gemini_summary = await _fetch_gemini_summary(
                    client, organization_id, organizer_email, title, start_time, meeting_id
                )

        except Exception as e:
            if "retry" in type(e).__name__.lower():
                raise
            logger.warning("Meet API recording check failed: %s", e)
            raise task.retry(exc=e)

        # Save whatever we have so far (participants, summary) even if
        # recordings/transcripts aren't ready yet — don't lose data on retry
        async with get_session(organization_id=organization_id) as session:
            meeting = await session.get(Meeting, UUID(meeting_id))
            if meeting:
                if recording_url:
                    meeting.recording_url = recording_url
                if recording_drive_id:
                    meeting.recording_drive_id = recording_drive_id
                if transcript_url:
                    meeting.transcript_url = transcript_url
                if participant_data:
                    meeting.participants = participant_data
                    meeting.participant_count = len(participant_data)
                if gemini_summary:
                    meeting.summary = gemini_summary
                await session.commit()

        if not recording_url and not transcript_url:
            remaining = task.max_retries - task.request.retries
            if remaining > 0:
                logger.info(
                    "No recordings/transcripts found yet for meeting %s (%d participants found), %d retries remaining",
                    meeting_id, len(participant_data), remaining,
                )
                raise task.retry()
            else:
                logger.info(
                    "Retries exhausted for meeting %s — saved %d participants + summary without recordings/transcripts",
                    meeting_id, len(participant_data),
                )

        if recording_url:
            await emit_event(
                event_type="huddle.recording_ready",
                organization_id=organization_id,
                data={
                    "meeting_id": meeting_id,
                    "recording_url": recording_url,
                    "drive_file_id": recording_drive_id,
                    "transcript_url": transcript_url,
                },
            )

        logger.info("Linked Meet API data to meeting %s (summary=%s)", meeting_id, bool(gemini_summary))
        return {
            "status": "found",
            "meeting_id": meeting_id,
            "recording_url": recording_url,
            "transcript_url": transcript_url,
            "participant_count": len(participant_data),
            "has_summary": bool(gemini_summary),
        }

    # ── Legacy fallback: Drive search for meetings without meet_space_name ──
    search_after = start_time.strftime("%Y-%m-%dT%H:%M:%S")
    search_before = (start_time + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    query = (
        f"(mimeType='video/mp4' or mimeType='video/webm') "
        f"and modifiedTime > '{search_after}' "
        f"and modifiedTime < '{search_before}' "
        f"and trashed=false"
    )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://www.googleapis.com/drive/v3/files",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "q": query,
                    "fields": "files(id,name,webViewLink,modifiedTime)",
                    "orderBy": "modifiedTime desc",
                    "pageSize": 20,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            files = resp.json().get("files", [])
    except Exception as e:
        logger.warning("Drive search failed for recording: %s", e)
        raise task.retry(exc=e)

    title_lower = title.lower()
    matched = None
    fallback = None
    for f in files:
        name_lower = f.get("name", "").lower()
        if title_lower != "huddle" and title_lower in name_lower:
            matched = f
            break
        if fallback is None and "meet" in name_lower:
            fallback = f
    if not matched:
        matched = fallback

    if not matched:
        remaining = task.max_retries - task.request.retries
        if remaining > 0:
            logger.info(
                "No recording found for meeting %s, %d retries remaining",
                meeting_id, remaining,
            )
            raise task.retry()
        return {"status": "not_found", "meeting_id": meeting_id}

    async with get_session(organization_id=organization_id) as session:
        meeting = await session.get(Meeting, UUID(meeting_id))
        if meeting:
            meeting.recording_url = matched.get("webViewLink", "")
            meeting.recording_drive_id = matched.get("id", "")
            await session.commit()

    await emit_event(
        event_type="huddle.recording_ready",
        organization_id=organization_id,
        data={
            "meeting_id": meeting_id,
            "recording_url": matched.get("webViewLink", ""),
            "drive_file_id": matched.get("id", ""),
            "file_name": matched.get("name", ""),
        },
    )

    logger.info("Linked recording %s to meeting %s", matched.get("id"), meeting_id)
    return {
        "status": "found",
        "meeting_id": meeting_id,
        "drive_file_id": matched.get("id", ""),
        "recording_url": matched.get("webViewLink", ""),
    }


async def _fetch_gemini_summary(
    client: "httpx.AsyncClient",
    organization_id: str,
    organizer_email: str | None,
    title: str,
    start_time: datetime,
    meeting_id: str,
) -> str:
    """Search Drive's 'Meet Recordings' folder for a Gemini-generated summary doc.

    For named meetings, Gemini uses the meeting title as the doc name.
    For huddles (title='Huddle'), the doc is named like 'Meeting started <timestamp>'.
    Returns the plain-text content of the doc, or empty string if not found.
    """
    # Get a Drive-scoped token (Calendar token won't have Drive access)
    drive_token = await _get_google_token(
        None, organization_id, organizer_email, preferred_connector="google_drive"
    )
    if not drive_token:
        logger.info("No Drive token available for Gemini summary fetch, meeting %s", meeting_id)
        return ""

    drive_headers = {"Authorization": f"Bearer {drive_token}"}
    DRIVE_API = "https://www.googleapis.com/drive/v3"

    # Build time window: summary appears shortly after meeting ends
    search_after = start_time.strftime("%Y-%m-%dT%H:%M:%S")
    search_before = (start_time + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S")

    # Search for the doc by name — try meeting title first, then fall back to
    # "Meeting started" (Gemini's default for huddles / unnamed meetings)
    name_filters = ["name contains 'Meeting started'"]
    if title and title.lower() != "huddle":
        name_filters.insert(0, f"name contains '{title}'")

    files = []
    try:
        for name_filter in name_filters:
            query = (
                f"mimeType='application/vnd.google-apps.document' "
                f"and {name_filter} "
                f"and modifiedTime > '{search_after}' "
                f"and modifiedTime < '{search_before}' "
                f"and trashed=false"
            )
            resp = await client.get(
                f"{DRIVE_API}/files",
                headers=drive_headers,
                params={
                    "q": query,
                    "fields": "files(id,name,modifiedTime)",
                    "orderBy": "modifiedTime desc",
                    "pageSize": 5,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            files = resp.json().get("files", [])
            if files:
                break

        if not files:
            logger.info("No Gemini summary doc found for meeting %s", meeting_id)
            return ""

        doc_id = files[0]["id"]
        doc_name = files[0].get("name", "")
        logger.info("Found Gemini summary doc '%s' (%s) for meeting %s", doc_name, doc_id, meeting_id)

        # Export as plain text
        export_resp = await client.get(
            f"{DRIVE_API}/files/{doc_id}/export",
            headers=drive_headers,
            params={"mimeType": "text/plain"},
            timeout=30.0,
        )
        export_resp.raise_for_status()
        summary_text = export_resp.text.strip()

        logger.info("Fetched Gemini summary (%d chars) for meeting %s", len(summary_text), meeting_id)
        return summary_text

    except Exception as e:
        logger.warning("Failed to fetch Gemini summary for meeting %s: %s", meeting_id, e)
        return ""


async def _get_google_token(
    task: Any,
    organization_id: str,
    organizer_email: str | None,
    preferred_connector: str | None = None,
) -> str | None:
    """Get a Google OAuth token for Meet/Drive API calls.

    Prefers the Calendar integration (same token works for Meet API).
    Falls back to Drive integration if Calendar is not available.
    Use preferred_connector='google_drive' to try Drive first (e.g. for Drive API calls).
    """
    from connectors.registry import discover_connectors
    from models.database import get_admin_session
    from models.integration import Integration
    from models.user import User
    from sqlalchemy import select

    connectors = discover_connectors()

    # Default order: Calendar first (covers Meet API), Drive fallback
    order = ["google_calendar", "google_drive"]
    if preferred_connector and preferred_connector in order:
        order.remove(preferred_connector)
        order.insert(0, preferred_connector)

    for connector_name in order:
        cls = connectors.get(connector_name)
        if not cls:
            continue

        integration = None

        if organizer_email:
            async with get_admin_session() as admin_session:
                result = await admin_session.execute(
                    select(Integration)
                    .join(User, Integration.user_id == User.id)
                    .where(
                        Integration.organization_id == UUID(organization_id),
                        Integration.connector == connector_name,
                        Integration.is_active == True,  # noqa: E712
                        User.email == organizer_email,
                    )
                )
                integration = result.scalars().first()

        if not integration:
            async with get_admin_session() as admin_session:
                result = await admin_session.execute(
                    select(Integration).where(
                        Integration.organization_id == UUID(organization_id),
                        Integration.connector == connector_name,
                        Integration.is_active == True,  # noqa: E712
                    )
                )
                integration = result.scalars().first()

        if integration:
            try:
                connector = cls(organization_id, user_id=str(integration.user_id))
                token, _ = await connector.get_oauth_token()
                return token
            except Exception as e:
                logger.warning("Failed to get %s token: %s", connector_name, e)
                continue

    return None


@celery_app.task(bind=True, name="workers.tasks.sync.sync_all_organizations")
def sync_all_organizations(self: Any) -> dict[str, Any]:
    """
    Celery task to sync all integrations for all organizations.
    
    This is the hourly sync task that runs via Beat schedule.
    
    Returns:
        Dict with summary of all sync operations
    """
    logger.info(f"Task {self.request.id}: Starting hourly sync for all organizations")
    
    async def _sync_all() -> dict[str, Any]:
        now = datetime.utcnow()
        all_integrations: list[dict[str, str | None]] = await _get_all_active_integrations()
        
        # Group by organization
        orgs: dict[str, list[dict[str, str | None]]] = {}
        for integration in all_integrations:
            org_id: str = integration["organization_id"]  # type: ignore[assignment]
            if org_id not in orgs:
                orgs[org_id] = []
            orgs[org_id].append(integration)
        
        results: dict[str, dict[str, Any]] = {}
        total_synced: int = 0
        total_failed: int = 0
        
        for org_id, entries in orgs.items():
            results[org_id] = {}
            for entry in entries:
                if not _should_sync_in_periodic_run(entry, now):
                    continue
                provider: str = entry["connector"]  # type: ignore[assignment]
                uid: str | None = entry["user_id"]
                key: str = f"{provider}:{uid}" if uid else provider
                result = await _sync_integration(org_id, provider, user_id=uid)
                results[org_id][key] = result
                if result["status"] == "completed":
                    total_synced += 1
                else:
                    total_failed += 1
        
        summary: dict[str, Any] = {
            "total_organizations": len(orgs),
            "total_integrations_synced": total_synced,
            "total_integrations_failed": total_failed,
            "started_at": datetime.utcnow().isoformat(),
            "results": results,
        }
        
        logger.info(
            f"Hourly sync complete: {total_synced} succeeded, {total_failed} failed"
        )
        return summary
    
    return run_async(_sync_all())


@celery_app.task(bind=True, name="workers.tasks.sync.sweep_active_huddles")
def sweep_active_huddles(self: Any) -> dict[str, Any]:
    """
    Periodic task that finds huddles still marked 'active' and checks
    whether their conference has actually ended. If so, marks them
    completed and schedules the recording/transcript check.
    """
    logger.info(f"Task {self.request.id}: Sweeping active huddles")
    return run_async(_sweep_active_huddles())


async def _sweep_active_huddles() -> dict[str, Any]:
    """Async implementation of the active huddle sweep."""
    import httpx
    from models.database import get_admin_session, get_session
    from models.meeting import Meeting
    from sqlalchemy import select

    MEET_API = "https://meet.googleapis.com/v2"

    # Find all meetings with huddle_status = 'active'
    async with get_admin_session() as session:
        result = await session.execute(
            select(Meeting).where(Meeting.huddle_status == "active")
        )
        active_huddles = result.scalars().all()

    if not active_huddles:
        return {"status": "ok", "checked": 0, "ended": 0}

    ended = 0
    checked = 0

    for meeting in active_huddles:
        checked += 1
        org_id = str(meeting.organization_id)
        meeting_id = str(meeting.id)

        # Skip huddles less than 5 minutes old (still likely in progress)
        if meeting.scheduled_start:
            age_minutes = (datetime.utcnow() - meeting.scheduled_start).total_seconds() / 60
            if age_minutes < 5:
                continue

        if meeting.meet_space_name:
            # New path: check Meet API for active conference
            token = await _get_google_token(None, org_id, meeting.organizer_email)
            if not token:
                logger.warning("No token for huddle sweep, meeting %s", meeting_id)
                continue

            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{MEET_API}/conferenceRecords",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                        params={"filter": f'space.name="{meeting.meet_space_name}"'},
                        timeout=30.0,
                    )
                    resp.raise_for_status()
                    records = resp.json().get("conferenceRecords", [])

                if records:
                    # Conference record exists — check if it has ended
                    latest = records[-1]
                    end_time = latest.get("endTime")
                    if end_time:
                        # Conference ended — mark meeting completed
                        now = datetime.utcnow()
                        async with get_session(organization_id=org_id) as session:
                            m = await session.get(Meeting, meeting.id)
                            if m and m.huddle_status == "active":
                                m.status = "completed"
                                m.huddle_status = "ended"
                                m.scheduled_end = now
                                if m.scheduled_start:
                                    m.duration_minutes = max(1, int((now - m.scheduled_start).total_seconds() / 60))
                                await session.commit()
                                ended += 1

                        # Schedule recording check
                        check_huddle_recording.apply_async(
                            args=[meeting_id, org_id],
                            countdown=300,
                        )
                        logger.info("Sweep: ended huddle %s, scheduled recording check", meeting_id)
                else:
                    # No conference record — nobody ever joined. If old enough (>30 min), clean up.
                    if meeting.scheduled_start:
                        age = (datetime.utcnow() - meeting.scheduled_start).total_seconds() / 60
                        if age > 30:
                            async with get_session(organization_id=org_id) as session:
                                m = await session.get(Meeting, meeting.id)
                                if m and m.huddle_status == "active":
                                    m.status = "cancelled"
                                    m.huddle_status = "ended"
                                    await session.commit()
                                    ended += 1
                            logger.info("Sweep: cancelled stale huddle %s (no one joined)", meeting_id)

            except Exception as e:
                logger.warning("Sweep: error checking huddle %s: %s", meeting_id, e)
                continue

        elif meeting.google_event_id:
            # Legacy path: check if calendar event end time has passed
            if meeting.scheduled_end and meeting.scheduled_end < datetime.utcnow():
                async with get_session(organization_id=org_id) as session:
                    m = await session.get(Meeting, meeting.id)
                    if m and m.huddle_status == "active":
                        m.status = "completed"
                        m.huddle_status = "ended"
                        if m.scheduled_start:
                            m.duration_minutes = max(1, int((m.scheduled_end - m.scheduled_start).total_seconds() / 60))
                        await session.commit()
                        ended += 1

                check_huddle_recording.apply_async(
                    args=[meeting_id, org_id],
                    countdown=300,
                )
                logger.info("Sweep: ended legacy huddle %s", meeting_id)

    logger.info("Sweep complete: checked=%d, ended=%d", checked, ended)
    return {"status": "ok", "checked": checked, "ended": ended}

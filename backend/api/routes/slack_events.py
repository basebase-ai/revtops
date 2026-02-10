"""
Slack Events API webhook endpoint.

Handles incoming events from Slack, including:
- URL verification challenge (when setting up the webhook)
- message.im events (DMs to the bot)
- app_mention events (@mentions in channels)
- message events in threads where the bot is already participating

NOTE: For thread replies to work, the Slack app must subscribe to
``message.channels`` (and ``message.groups`` for private channels)
under Event Subscriptions at https://api.slack.com/apps.

Security:
- All requests are verified using HMAC-SHA256 signature
- Timestamps are validated to prevent replay attacks
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import time
from typing import Any

import redis.asyncio as redis
from fastapi import APIRouter, HTTPException, Request, Response

from config import settings
from services.slack_conversations import (
    persist_slack_message_activity,
    process_slack_dm,
    process_slack_mention,
    process_slack_thread_reply,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Redis client for event deduplication
_redis_client: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """Get or create Redis client for event deduplication."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL)
    return _redis_client


def verify_slack_signature(
    body: bytes,
    timestamp: str,
    signature: str,
) -> bool:
    """
    Verify that the request came from Slack using HMAC-SHA256.
    
    Args:
        body: Raw request body
        timestamp: X-Slack-Request-Timestamp header
        signature: X-Slack-Signature header
        
    Returns:
        True if signature is valid
    """
    if not settings.SLACK_SIGNING_SECRET:
        logger.warning("[slack_events] SLACK_SIGNING_SECRET not configured")
        return False
    
    # Check timestamp to prevent replay attacks (5 minute window)
    try:
        request_time = int(timestamp)
        current_time = int(time.time())
        if abs(current_time - request_time) > 300:  # 5 minutes
            logger.warning("[slack_events] Request timestamp too old: %s", timestamp)
            return False
    except ValueError:
        logger.warning("[slack_events] Invalid timestamp: %s", timestamp)
        return False
    
    # Compute expected signature
    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected_signature = (
        "v0="
        + hmac.new(
            settings.SLACK_SIGNING_SECRET.encode("utf-8"),
            sig_basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )
    
    # Compare signatures using constant-time comparison
    return hmac.compare_digest(expected_signature, signature)


async def is_duplicate_event(event_id: str) -> bool:
    """
    Check if we've already processed this event (deduplication).
    
    Slack may retry events if we don't respond quickly enough.
    We use Redis to track processed event IDs with a 1-hour TTL.
    
    Args:
        event_id: Unique event identifier from Slack
        
    Returns:
        True if event was already processed
    """
    try:
        redis_client = await get_redis()
        key = f"revtops:slack_events:{event_id}"
        
        # Try to set the key with NX (only if not exists)
        # Returns True if key was set, False if it already existed
        was_set = await redis_client.set(key, "1", nx=True, ex=3600)  # 1 hour TTL
        return not was_set
    except Exception as e:
        logger.error("[slack_events] Redis error during deduplication: %s", e)
        # If Redis fails, process the event anyway (better to duplicate than miss)
        return False


@router.post("/events", response_model=None)
async def handle_slack_events(request: Request) -> Response | dict[str, Any]:
    """
    Handle incoming Slack Events API requests.
    
    This endpoint handles:
    1. URL verification challenge (returns challenge value)
    2. Event callbacks (processes events asynchronously)
    
    All requests are verified using HMAC-SHA256 signature.
    """
    # Read raw body for signature verification
    body = await request.body()
    
    # Verify signature
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    
    if not verify_slack_signature(body, timestamp, signature):
        logger.warning("[slack_events] Invalid signature")
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Parse JSON payload
    try:
        payload: dict[str, Any] = await request.json()
    except Exception as e:
        logger.error("[slack_events] Failed to parse JSON: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    event_type = payload.get("type")
    
    # Handle URL verification challenge
    if event_type == "url_verification":
        challenge = payload.get("challenge", "")
        logger.info("[slack_events] URL verification challenge received")
        return {"challenge": challenge}
    
    # Handle event callbacks
    if event_type == "event_callback":
        event = payload.get("event", {})
        event_id = payload.get("event_id", "")
        team_id = payload.get("team_id", "")
        
        # Deduplication check
        if event_id and await is_duplicate_event(event_id):
            logger.info("[slack_events] Skipping duplicate event: %s", event_id)
            return {"ok": True, "duplicate": True}
        
        # Process the event
        inner_type = event.get("type")
        
        # Handle DMs to the bot
        if inner_type == "message":
            # Check if this is a DM (im) channel type
            channel_type = event.get("channel_type")
            
            # Skip bot messages to prevent infinite loops
            if event.get("bot_id") or event.get("subtype") == "bot_message":
                logger.debug("[slack_events] Skipping bot message")
                return {"ok": True}
            
            # Skip message edits/deletes
            if event.get("subtype") in ("message_changed", "message_deleted"):
                logger.debug("[slack_events] Skipping message edit/delete")
                return {"ok": True}

            # Persist every channel message as an activity in real-time
            # so the bot always has up-to-date Slack data to query.
            if channel_type != "im" and event.get("text", "").strip():
                asyncio.create_task(
                    persist_slack_message_activity(
                        team_id=team_id,
                        channel_id=event.get("channel", ""),
                        user_id=event.get("user", ""),
                        message_text=event.get("text", ""),
                        ts=event.get("ts", ""),
                        thread_ts=event.get("thread_ts"),
                    )
                )

            if channel_type == "im":
                # --- Direct messages ---
                channel_id: str = event.get("channel", "")
                user_id: str = event.get("user", "")
                text: str = event.get("text", "")
                event_ts: str = event.get("event_ts", "")
                
                if not text.strip():
                    logger.debug("[slack_events] Skipping empty message")
                    return {"ok": True}
                
                logger.info(
                    "[slack_events] Processing DM from %s in %s: %s",
                    user_id,
                    channel_id,
                    text[:50]
                )
                
                # Process asynchronously to respond quickly to Slack
                # Slack expects a response within 3 seconds
                asyncio.create_task(
                    process_slack_dm(
                        team_id=team_id,
                        channel_id=channel_id,
                        user_id=user_id,
                        message_text=text,
                        event_ts=event_ts,
                    )
                )
                
                return {"ok": True}

            # --- Thread replies in channels (no @mention required) ---
            # Requires "message.channels" / "message.groups" event subscriptions
            # in the Slack app configuration.
            thread_ts: str | None = event.get("thread_ts")
            if channel_type != "im" and thread_ts:
                channel_id = event.get("channel", "")
                user_id = event.get("user", "")
                text = event.get("text", "")

                if not text.strip():
                    logger.debug("[slack_events] Skipping empty thread reply")
                    return {"ok": True}

                logger.info(
                    "[slack_events] Processing thread reply from %s in %s (thread %s): %s",
                    user_id,
                    channel_id,
                    thread_ts,
                    text[:50],
                )

                asyncio.create_task(
                    process_slack_thread_reply(
                        team_id=team_id,
                        channel_id=channel_id,
                        user_id=user_id,
                        message_text=text,
                        thread_ts=thread_ts,
                        event_ts=event.get("ts", ""),
                    )
                )

                return {"ok": True}
        
        # Handle @mentions in channels
        if inner_type == "app_mention":
            channel_id = event.get("channel", "")
            user_id = event.get("user", "")
            text = event.get("text", "")
            event_ts = event.get("event_ts", "")
            thread_ts = event.get("thread_ts")  # Reply in thread if already in one
            
            # Strip the @mention from the text (e.g., "<@U123ABC> what's my pipeline?")
            text = re.sub(r'<@[A-Z0-9]+>\s*', '', text).strip()
            
            if not text:
                logger.debug("[slack_events] Skipping empty mention")
                return {"ok": True}
            
            logger.info(
                "[slack_events] Processing @mention from %s in %s: %s",
                user_id,
                channel_id,
                text[:50]
            )
            
            # Process asynchronously - reply in thread (use event_ts if not already in thread)
            asyncio.create_task(
                process_slack_mention(
                    team_id=team_id,
                    channel_id=channel_id,
                    user_id=user_id,
                    message_text=text,
                    thread_ts=thread_ts or event_ts,  # Reply in thread
                )
            )
            
            return {"ok": True}
        
        # Log unhandled event types
        logger.debug("[slack_events] Unhandled event type: %s", inner_type)
    
    return {"ok": True}


@router.get("/events/health")
async def slack_events_health() -> dict[str, Any]:
    """Health check for Slack events endpoint."""
    return {
        "status": "ok",
        "signing_secret_configured": bool(settings.SLACK_SIGNING_SECRET),
    }

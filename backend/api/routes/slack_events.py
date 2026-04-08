"""
Slack Events API webhook endpoint.

Handles incoming events from Slack, including:
- URL verification challenge (when setting up the webhook)
- message.im/message.mpim events (1:1 and multi-person DMs to the bot)
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
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from config import get_redis_connection_kwargs, settings
from messengers.base import InboundMessage, MessageType
from messengers.slack import SlackMessenger

logger = logging.getLogger(__name__)

router = APIRouter()

# Redis client for event deduplication
_redis_client: redis.Redis | None = None


class SlackThreadLockManager:
    """In-process async lock manager keyed by Slack thread identity."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_refs: dict[str, int] = {}
        self._manager_lock = asyncio.Lock()

    @staticmethod
    def build_lock_key(team_id: str, channel_id: str, thread_ts: str) -> str:
        return f"{team_id}:{channel_id}:{thread_ts}"

    @asynccontextmanager
    async def thread_lock(self, lock_key: str):
        """Acquire/release the per-thread lock, cleaning up idle keys."""
        async with self._manager_lock:
            lock = self._locks.get(lock_key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[lock_key] = lock
                self._lock_refs[lock_key] = 0
                logger.debug("[slack_events] Created thread lock key=%s", lock_key)
            self._lock_refs[lock_key] = self._lock_refs.get(lock_key, 0) + 1
            queued_count = self._lock_refs[lock_key]

        logger.info(
            "[slack_events] Waiting for thread lock key=%s queued=%d",
            lock_key,
            queued_count,
        )
        await lock.acquire()
        logger.info("[slack_events] Acquired thread lock key=%s", lock_key)

        try:
            yield
        finally:
            lock.release()
            logger.info("[slack_events] Released thread lock key=%s", lock_key)
            async with self._manager_lock:
                remaining = max(self._lock_refs.get(lock_key, 1) - 1, 0)
                if remaining == 0:
                    self._lock_refs.pop(lock_key, None)
                    self._locks.pop(lock_key, None)
                    logger.debug(
                        "[slack_events] Removed idle thread lock key=%s",
                        lock_key,
                    )
                else:
                    self._lock_refs[lock_key] = remaining


_thread_lock_manager = SlackThreadLockManager()


async def get_redis() -> redis.Redis:
    """Get or create Redis client for event deduplication."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            settings.REDIS_URL, **get_redis_connection_kwargs()
        )
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


async def is_duplicate_message(channel_id: str, message_ts: str) -> bool:
    """
    Cross-event-type dedup for the same Slack message.

    When a user @mentions the bot in a thread, Slack fires *two* events with
    different ``event_id`` values:
      1. ``app_mention`` — handled by :func:`process_slack_mention`
      2. ``message``     — handled by :func:`process_slack_thread_reply`

    Both events share the same ``channel`` + ``ts`` (the message timestamp).
    This function claims a Redis lock on ``channel:ts`` so only the first
    event to arrive gets processed; the second is skipped.

    Uses a short TTL (5 minutes) since the two events arrive within seconds.

    Args:
        channel_id: Slack channel ID
        message_ts: Message timestamp (``event.ts``)

    Returns:
        True if this message was already claimed by another event type
    """
    if not channel_id or not message_ts:
        return False
    try:
        redis_client = await get_redis()
        key: str = f"revtops:slack_msg_dedup:{channel_id}:{message_ts}"
        was_set: bool | None = await redis_client.set(key, "1", nx=True, ex=300)
        return not was_set
    except Exception as e:
        logger.error("[slack_events] Redis error during message dedup: %s", e)
        return False


async def cache_incoming_event_payload(
    body: bytes,
    payload: dict[str, Any],
) -> bool:
    """Cache the incoming Slack payload in Redis before responding to Slack."""
    event_id = payload.get("event_id")
    event = payload.get("event") or {}
    event_ts = event.get("event_ts") or event.get("ts") or "unknown"
    key_suffix = event_id or hashlib.sha256(body).hexdigest()
    cache_key = f"revtops:slack_events:incoming:{key_suffix}"

    logger.info(
        "[slack_events] Caching incoming event payload key=%s event_id=%s team_id=%s event_ts=%s",
        cache_key,
        event_id,
        payload.get("team_id"),
        event_ts,
    )

    cache_value = {
        "event_id": event_id,
        "event_type": payload.get("type"),
        "team_id": payload.get("team_id"),
        "received_at": int(time.time()),
        "payload": payload,
    }

    try:
        redis_client = await get_redis()
        was_set = await asyncio.wait_for(
            redis_client.set(
                cache_key,
                json.dumps(cache_value),
                ex=86400,  # 24h TTL
            ),
            timeout=1.0,
        )
        if not was_set:
            logger.warning(
                "[slack_events] Redis SET returned falsy response while caching key=%s",
                cache_key,
            )
        return bool(was_set)
    except asyncio.TimeoutError:
        logger.warning(
            "[slack_events] Timed out (>1s) while caching incoming payload key=%s",
            cache_key,
        )
        return False
    except Exception:
        logger.exception(
            "[slack_events] Failed to cache incoming payload key=%s",
            cache_key,
        )
        return False


async def _process_event_callback(payload: dict[str, Any]) -> None:
    """
    Process an event_callback in the background. Runs dedup check then dispatches.
    Slack expects 200 within 3 seconds, so this must NOT block the HTTP response.
    """
    try:
        await _process_event_callback_impl(payload)
    except Exception as e:
        logger.exception("[slack_events] Background processing failed: %s", e)


async def _process_event_callback_impl(payload: dict[str, Any]) -> None:
    """Implementation of event_callback processing (called from background task)."""
    event: dict[str, Any] = payload.get("event", {})
    event_id: str = payload.get("event_id", "")
    team_id: str = payload.get("team_id", "")
    bot_user_ids: set[str] = _extract_bot_user_ids(payload)

    if event_id and await is_duplicate_event(event_id):
        logger.info("[slack_events] Skipping duplicate event: %s", event_id)
        return

    inner_type: str | None = event.get("type")
    messenger = SlackMessenger()

    if inner_type == "message":
        channel_type: str | None = event.get("channel_type")
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return
        if event.get("subtype") in ("message_changed", "message_deleted"):
            return

        # Persist non-DM channel messages as Activity rows
        if channel_type != "im" and event.get("text", "").strip():
            activity_msg: InboundMessage = _build_inbound_message(
                event, team_id, MessageType.MENTION,
            )
            asyncio.create_task(
                _persist_activity(messenger, activity_msg, team_id)
            )

        is_direct_message: bool = channel_type in {"im", "mpim"}
        if is_direct_message:
            channel_id: str = event.get("channel", "")
            user_id: str = event.get("user", "")
            text: str = event.get("text", "")
            message_ts: str = event.get("ts") or event.get("event_ts", "")
            thread_ts: str | None = event.get("thread_ts")
            files: list[dict[str, Any]] = event.get("files", [])
            if not text.strip() and not files:
                return
            logger.info(
                "[slack_events] Processing direct message type=%s from %s in %s thread=%s: %s (files=%d)",
                channel_type, user_id, channel_id, thread_ts, text[:50], len(files),
            )
            lock_key: str = SlackThreadLockManager.build_lock_key(
                team_id,
                channel_id,
                thread_ts,
            )
            async with _thread_lock_manager.thread_lock(lock_key):
                message = _build_inbound_message(
                    event, team_id, MessageType.DIRECT, bot_user_ids=bot_user_ids,
                )
                await messenger.process_inbound(message)
            return

        thread_ts = event.get("thread_ts")
        if channel_type != "im" and thread_ts:
            channel_id = event.get("channel", "")
            user_id = event.get("user", "")
            text = event.get("text", "")
            message_ts = event.get("ts", "")
            files = event.get("files", [])
            if not text.strip() and not files:
                return

            if _message_mentions_bot_user(text, bot_user_ids):
                if message_ts and channel_id and await is_duplicate_message(channel_id, message_ts):
                    logger.info(
                        "[slack_events] Skipping duplicate message %s:%s (already claimed by another event type)",
                        channel_id, message_ts,
                    )
                    return

                normalized_text: str = _strip_bot_mentions(text, bot_user_ids)
                logger.info(
                    "[slack_events] Routing in-thread bot mention to mention handler user=%s channel=%s thread=%s",
                    user_id, channel_id, thread_ts,
                )
                lock_key: str = SlackThreadLockManager.build_lock_key(team_id, channel_id, thread_ts)
                async with _thread_lock_manager.thread_lock(lock_key):
                    message = _build_inbound_message(
                        event,
                        team_id,
                        MessageType.MENTION,
                        text_override=normalized_text,
                        bot_user_ids=bot_user_ids,
                    )
                    await messenger.process_inbound(message)
                return

            if message_ts and channel_id and await is_duplicate_message(channel_id, message_ts):
                logger.info(
                    "[slack_events] Skipping duplicate message %s:%s (already claimed by another event type)",
                    channel_id, message_ts,
                )
                return

            logger.info(
                "[slack_events] Processing thread reply from %s in %s (thread %s): %s (files=%d)",
                user_id, channel_id, thread_ts, text[:50], len(files),
            )
            lock_key = SlackThreadLockManager.build_lock_key(team_id, channel_id, thread_ts)
            async with _thread_lock_manager.thread_lock(lock_key):
                message = _build_inbound_message(
                    event, team_id, MessageType.THREAD_REPLY, bot_user_ids=bot_user_ids,
                )
                await messenger.process_inbound(message)
            return

    if inner_type == "app_mention":
        channel_id = event.get("channel", "")
        user_id = event.get("user", "")
        text = re.sub(r"<@[A-Z0-9]+>\s*", "", event.get("text", "")).strip()
        event_ts: str = event.get("event_ts", "")
        message_ts = event.get("ts", "") or event_ts
        thread_ts = event.get("thread_ts")
        files = event.get("files", [])
        if not text and not files:
            return

        if message_ts and channel_id and await is_duplicate_message(channel_id, message_ts):
            logger.info(
                "[slack_events] Skipping duplicate message %s:%s (already claimed by another event type)",
                channel_id, message_ts,
            )
            return

        logger.info("[slack_events] Processing @mention from %s in %s: %s (files=%d)", user_id, channel_id, text[:50], len(files))
        lock_thread_ts: str = thread_ts or event_ts
        lock_key = SlackThreadLockManager.build_lock_key(team_id, channel_id, lock_thread_ts)
        async with _thread_lock_manager.thread_lock(lock_key):
            message = _build_inbound_message(
                event,
                team_id,
                MessageType.MENTION,
                text_override=text,
                thread_ts_override=lock_thread_ts,
                bot_user_ids=bot_user_ids,
            )
            await messenger.process_inbound(message)


def _build_inbound_message(
    event: dict[str, Any],
    team_id: str,
    message_type: MessageType,
    *,
    text_override: str | None = None,
    thread_ts_override: str | None = None,
    bot_user_ids: set[str] | None = None,
) -> InboundMessage:
    """Build an :class:`InboundMessage` from a Slack event payload."""
    channel_id: str = event.get("channel", "")
    user_id: str = event.get("user", "")
    text: str = text_override if text_override is not None else event.get("text", "")
    event_ts: str = event.get("event_ts", "") or event.get("ts", "")
    message_ts: str = event.get("ts", "") or event_ts
    thread_ts: str | None = thread_ts_override or event.get("thread_ts")
    files: list[dict[str, Any]] = event.get("files", [])
    raw_text: str = event.get("text", "") or ""
    mentions: list[dict[str, Any]] = (
        _extract_mentions_from_slack_text(raw_text, bot_user_ids)
        if bot_user_ids is not None
        else []
    )

    return InboundMessage(
        external_user_id=user_id,
        text=text,
        message_type=message_type,
        raw_attachments=files,
        messenger_context={
            "workspace_id": team_id,
            "channel_id": channel_id,
            "thread_id": thread_ts,
            "thread_ts": thread_ts,
            "event_ts": event_ts,
            "channel_type": event.get("channel_type"),
        },
        message_id=message_ts,
        mentions=mentions,
    )


async def _persist_activity(
    messenger: SlackMessenger,
    message: InboundMessage,
    team_id: str,
) -> None:
    """Persist a channel message as an Activity row via the messenger."""
    try:
        org_id: str | None = await messenger._resolve_org_from_workspace(team_id)
        if org_id:
            await messenger.persist_channel_activity(message, org_id)
    except Exception as exc:
        logger.error("[slack_events] Failed to persist activity: %s", exc)


# Slack encodes @-mentions as ``<@USERID>`` or ``<@USERID|display>`` in message text.
_SLACK_USER_MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]+)?>")


def _extract_mentions_from_slack_text(
    text: str,
    bot_user_ids: set[str],
) -> list[dict[str, Any]]:
    """
    Parse Slack ``<@…>`` tokens and classify each as agent (this bot) or user.

    When ``bot_user_ids`` is empty we cannot distinguish the bot from humans, so
    we return no structured mentions (plain messages still rely on conversation state).
    """
    if not text.strip() or not bot_user_ids:
        return []
    mentions: list[dict[str, Any]] = []
    for match in _SLACK_USER_MENTION_RE.finditer(text):
        slack_uid: str = match.group(1)
        if slack_uid in bot_user_ids:
            mentions.append({"type": "agent"})
        else:
            mentions.append({"type": "user", "external_user_id": slack_uid})
    return mentions


def _extract_bot_user_ids(payload: dict[str, Any]) -> set[str]:
    """Extract known bot user IDs for this event payload."""
    user_ids: set[str] = set()

    authed_users = payload.get("authed_users")
    if isinstance(authed_users, list):
        for candidate in authed_users:
            if isinstance(candidate, str) and candidate:
                user_ids.add(candidate)

    authorizations = payload.get("authorizations")
    if isinstance(authorizations, list):
        for authorization in authorizations:
            if not isinstance(authorization, dict):
                continue
            candidate = authorization.get("user_id")
            if isinstance(candidate, str) and candidate:
                user_ids.add(candidate)

    return user_ids


def _message_mentions_bot_user(text: str, bot_user_ids: set[str]) -> bool:
    """Return True when message text contains an explicit mention of this bot."""
    if not text or not bot_user_ids:
        return False
    return any(f"<@{bot_user_id}>" in text for bot_user_id in bot_user_ids)


def _strip_bot_mentions(text: str, bot_user_ids: set[str]) -> str:
    """Normalize message text by removing all direct mentions of this bot."""
    normalized: str = text
    for bot_user_id in bot_user_ids:
        normalized = re.sub(rf"<@{re.escape(bot_user_id)}>\s*", "", normalized)
    return normalized.strip()


@router.post("/events", response_model=None)
async def handle_slack_events(request: Request) -> dict[str, Any] | JSONResponse:
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
    
    # Parse JSON from body (body already read for signature verification)
    try:
        payload: dict[str, Any] = json.loads(body.decode("utf-8"))
    except Exception as e:
        logger.error("[slack_events] Failed to parse JSON: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    event_type = payload.get("type")
    
    # Handle URL verification challenge
    if event_type == "url_verification":
        challenge = payload.get("challenge", "")
        logger.info("[slack_events] URL verification challenge received")
        return {"challenge": challenge}
    
    # Handle event callbacks: return 200 immediately to satisfy Slack's 3-second timeout.
    # Processing (including dedup) runs in the background to avoid blocking the response.
    if event_type == "event_callback":
        cache_succeeded = await cache_incoming_event_payload(body, payload)
        asyncio.create_task(_process_event_callback(payload))
        status_code = 200 if cache_succeeded else 202
        logger.info(
            "[slack_events] Responding to Slack event_callback with status=%s cache_succeeded=%s",
            status_code,
            cache_succeeded,
        )
        return JSONResponse(content={"ok": True}, status_code=status_code)

    return {"ok": True}


@router.get("/events/health")
async def slack_events_health() -> dict[str, Any]:
    """Health check for Slack events endpoint."""
    return {
        "status": "ok",
        "signing_secret_configured": bool(settings.SLACK_SIGNING_SECRET),
    }

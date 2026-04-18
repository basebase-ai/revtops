"""
Microsoft Teams Bot Framework webhook endpoint.

Handles incoming activities from the Bot Framework (Teams channel):
- message: 1:1 chat, channel @mentions, thread replies
- conversationUpdate / installationUpdate: acknowledge only

Security: Requests are validated using the Bot Framework JWT Bearer token.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
import redis.asyncio as redis
from fastapi import APIRouter, HTTPException, Request
from jose import JWTError, jwk, jwt

from config import get_redis_connection_kwargs, settings
from messengers.base import InboundMessage, MessageType
from messengers.teams import TeamsMessenger

logger = logging.getLogger(__name__)

router = APIRouter()

BOT_OPENID_URL: str = "https://login.botframework.com/v1/.well-known/openidconfiguration"
JWKS_CACHE_TTL_SECONDS: int = 3600

# Merged key set from Bot Framework + tenant-specific OpenID endpoints
_merged_jwks_keys: list[dict[str, Any]] = []
_jwks_fetched_at: float = 0.0

_redis_client: redis.Redis | None = None


class TeamsThreadLockManager:
    """Per-thread lock for Teams conversation to serialize replies."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_refs: dict[str, int] = {}
        self._manager_lock = asyncio.Lock()

    @staticmethod
    def build_lock_key(tenant_id: str, conversation_id: str, thread_id: str | None) -> str:
        if thread_id:
            return f"{tenant_id}:{conversation_id}:{thread_id}"
        return f"{tenant_id}:{conversation_id}"

    @asynccontextmanager
    async def thread_lock(self, lock_key: str):
        async with self._manager_lock:
            lock = self._locks.get(lock_key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[lock_key] = lock
                self._lock_refs[lock_key] = 0
            self._lock_refs[lock_key] = self._lock_refs.get(lock_key, 0) + 1
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()
            async with self._manager_lock:
                remaining = max(self._lock_refs.get(lock_key, 1) - 1, 0)
                if remaining == 0:
                    self._lock_refs.pop(lock_key, None)
                    self._locks.pop(lock_key, None)
                else:
                    self._lock_refs[lock_key] = remaining


_thread_lock_manager: TeamsThreadLockManager = TeamsThreadLockManager()


async def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            settings.REDIS_URL, **get_redis_connection_kwargs()
        )
    return _redis_client


async def _fetch_jwks_from_openid(client: httpx.AsyncClient, openid_url: str) -> list[dict[str, Any]]:
    """Fetch JWKS keys from an OpenID configuration endpoint."""
    resp = await client.get(openid_url, timeout=10.0)
    resp.raise_for_status()
    openid: dict[str, Any] = resp.json()
    jwks_uri: str | None = openid.get("jwks_uri")
    if not jwks_uri:
        return []
    resp2 = await client.get(jwks_uri, timeout=10.0)
    resp2.raise_for_status()
    return resp2.json().get("keys", [])


async def _refresh_jwks() -> None:
    """Fetch and merge JWKS from Bot Framework + tenant-specific OpenID endpoints."""
    global _merged_jwks_keys, _jwks_fetched_at
    now: float = time.monotonic()
    if _merged_jwks_keys and (now - _jwks_fetched_at) < JWKS_CACHE_TTL_SECONDS:
        return

    all_keys: list[dict[str, Any]] = []
    seen_kids: set[str] = set()

    async with httpx.AsyncClient() as client:
        # 1. Bot Framework OpenID (multi-tenant / emulator tokens)
        try:
            keys = await _fetch_jwks_from_openid(client, BOT_OPENID_URL)
            for k in keys:
                kid = k.get("kid", "")
                if kid and kid not in seen_kids:
                    all_keys.append(k)
                    seen_kids.add(kid)
        except Exception as exc:
            logger.warning("[teams_events] Failed to fetch Bot Framework JWKS: %s", exc)

        # 2. Tenant-specific OpenID (single-tenant bot tokens)
        tenant_id: str | None = settings.MICROSOFT_TENANT_ID
        if tenant_id:
            tenant_openid_url: str = (
                f"https://login.microsoftonline.com/{tenant_id}/v2.0/"
                ".well-known/openid-configuration"
            )
            try:
                keys = await _fetch_jwks_from_openid(client, tenant_openid_url)
                for k in keys:
                    kid = k.get("kid", "")
                    if kid and kid not in seen_kids:
                        all_keys.append(k)
                        seen_kids.add(kid)
            except Exception as exc:
                logger.warning("[teams_events] Failed to fetch tenant JWKS: %s", exc)

        # 3. Common Azure AD fallback (covers both single/multi tenant edge cases)
        common_openid_url: str = (
            "https://login.microsoftonline.com/common/v2.0/"
            ".well-known/openid-configuration"
        )
        try:
            keys = await _fetch_jwks_from_openid(client, common_openid_url)
            for k in keys:
                kid = k.get("kid", "")
                if kid and kid not in seen_kids:
                    all_keys.append(k)
                    seen_kids.add(kid)
        except Exception as exc:
            logger.debug("[teams_events] Failed to fetch common JWKS: %s", exc)

    if not all_keys:
        raise RuntimeError("Could not fetch any JWKS keys")

    _merged_jwks_keys = all_keys
    _jwks_fetched_at = now


def _verify_teams_jwt(token: str) -> dict[str, Any]:
    """Verify Bot Framework Bearer token and return claims.

    Uses merged JWKS from Bot Framework + Azure AD endpoints.
    Validates audience and expiry; issuer is not strictly checked
    because single-tenant and multi-tenant tokens use different issuers.
    """
    app_id: str | None = settings.MICROSOFT_APP_ID
    if not app_id:
        raise ValueError("MICROSOFT_APP_ID not configured")

    if not _merged_jwks_keys:
        raise ValueError("JWKS not available; call _refresh_jwks() first")

    unverified = jwt.get_unverified_header(token)
    kid: str | None = unverified.get("kid")
    if not kid:
        raise ValueError("Token missing kid")

    signing_key_pem: str | None = None
    for key in _merged_jwks_keys:
        if key.get("kid") == kid:
            alg: str = key.get("alg") or unverified.get("alg") or "RS256"
            signing_key_pem = jwk.construct(key, algorithm=alg).to_pem().decode("utf-8")
            break
    if not signing_key_pem:
        raise ValueError("Unknown signing key")

    payload: dict[str, Any] = jwt.decode(
        token,
        signing_key_pem,
        algorithms=["RS256", "RS384", "RS512"],
        audience=app_id,
        options={"verify_aud": True, "verify_exp": True, "verify_iss": False},
    )
    return payload


async def verify_teams_request(request: Request) -> None:
    """Validate Authorization Bearer token. Raises HTTPException on failure."""
    auth: str | None = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token: str = auth[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")
    try:
        await _refresh_jwks()
    except Exception as e:
        logger.warning("[teams_events] Failed to fetch JWKS: %s", e)
        raise HTTPException(status_code=503, detail="Auth metadata unavailable")
    try:
        # Run blocking JWT verify in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: _verify_teams_jwt(token),
        )
    except ValueError as e:
        logger.warning("[teams_events] JWT validation failed: %s", e)
        raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError as e:
        logger.warning("[teams_events] JWT error: %s", e)
        raise HTTPException(status_code=401, detail="Invalid token")


async def is_duplicate_activity(activity_id: str) -> bool:
    try:
        r = await get_redis()
        key = f"revtops:teams_events:{activity_id}"
        was_set = await r.set(key, "1", nx=True, ex=3600)
        return not was_set
    except Exception as e:
        logger.error("[teams_events] Redis dedup error: %s", e)
        return False


def _tenant_id_from_activity(activity: dict[str, Any]) -> str | None:
    channel_data: dict[str, Any] | None = activity.get("channelData") or {}
    tenant: dict[str, Any] | None = channel_data.get("tenant") if isinstance(channel_data, dict) else None
    if isinstance(tenant, dict):
        tid: str | None = tenant.get("id")
        if tid:
            return tid
    return None


def _build_inbound_message(
    activity: dict[str, Any],
    message_type: MessageType,
    *,
    text_override: str | None = None,
) -> InboundMessage:
    """Build InboundMessage from a Bot Framework message activity."""
    from_obj: dict[str, Any] = activity.get("from") or {}
    user_id: str = from_obj.get("aadObjectId") or from_obj.get("id") or ""
    conversation: dict[str, Any] = activity.get("conversation") or {}
    conv_id: str = conversation.get("id") or ""
    conversation_type: str = (conversation.get("conversationType") or "").strip().lower()
    tenant_id: str | None = _tenant_id_from_activity(activity)
    workspace_id: str = tenant_id or ""
    service_url: str = (activity.get("serviceUrl") or "").strip()
    recipient: dict[str, Any] = activity.get("recipient") or {}
    bot_id: str | None = recipient.get("id")
    reply_to_id: str | None = activity.get("replyToId")
    text: str = text_override if text_override is not None else (activity.get("text") or "")
    message_id: str = activity.get("id") or ""
    attachments: list[dict[str, Any]] = activity.get("attachments") or []

    return InboundMessage(
        external_user_id=user_id,
        text=text,
        message_type=message_type,
        raw_attachments=attachments,
        messenger_context={
            "workspace_id": workspace_id,
            "channel_id": conv_id,
            "thread_id": reply_to_id,
            "thread_ts": reply_to_id,
            "event_ts": message_id,
            "channel_type": conversation_type,
            "service_url": service_url,
            "bot_id": bot_id,
        },
        message_id=message_id,
    )


def _strip_mentions(text: str, bot_id: str | None) -> str:
    """Remove <at>bot_id</at> style mentions from Teams text."""
    if not text or not bot_id:
        return text.strip()
    pattern = re.compile(
        re.escape("<at>") + ".*?" + re.escape("</at>"),
        re.IGNORECASE | re.DOTALL,
    )
    cleaned = pattern.sub("", text)
    return cleaned.strip()


def _activity_mentions_bot(activity: dict[str, Any], bot_id: str | None) -> bool:
    if not bot_id:
        return False
    entities: list[dict[str, Any]] = activity.get("entities") or []
    for e in entities:
        if not isinstance(e, dict) or (e.get("type") or "").lower() != "mention":
            continue
        mentioned: dict[str, Any] | None = e.get("mentioned")
        if isinstance(mentioned, dict) and mentioned.get("id") == bot_id:
            return True
        if e.get("id") == bot_id:
            return True
    return False


def _is_public_teams_channel_message(activity: dict[str, Any]) -> bool:
    """Return True only for standard/public Teams channel messages."""
    conversation: dict[str, Any] = activity.get("conversation") or {}
    conversation_type: str = (conversation.get("conversationType") or "").strip().lower()
    if conversation_type != "channel":
        return False

    channel_data: dict[str, Any] = activity.get("channelData") or {}
    channel_raw: Any = channel_data.get("channel") if isinstance(channel_data, dict) else {}
    channel: dict[str, Any] = channel_raw if isinstance(channel_raw, dict) else {}
    membership_type: str = (channel.get("membershipType") or "").strip().lower()

    # Teams exposes "standard" for public channels; private/shared channels should not be persisted.
    if membership_type and membership_type != "standard":
        logger.info(
            "[teams_events] Skipping activity persistence for non-public channel membership_type=%s conversation_id=%s",
            membership_type,
            conversation.get("id", ""),
        )
        return False

    return True


async def _persist_activity(message: InboundMessage, tenant_id: str) -> None:
    """Persist a Teams channel message as an Activity row for analytics."""
    try:
        org_id: str | None = await TeamsMessenger()._resolve_org_from_workspace(tenant_id)
        if org_id:
            await TeamsMessenger().persist_channel_activity(message, org_id)
    except Exception as exc:
        logger.error("[teams_events] Failed to persist activity: %s", exc)


async def _process_message_activity(activity: dict[str, Any]) -> None:
    """Handle message activity: route to DIRECT, MENTION, or THREAD_REPLY."""
    try:
        conversation: dict[str, Any] = activity.get("conversation") or {}
        conversation_type: str = (conversation.get("conversationType") or "").strip().lower()
        is_group_raw: Any = conversation.get("isGroup")
        is_group: bool = is_group_raw is True or (
            isinstance(is_group_raw, str) and is_group_raw.lower() == "true"
        )
        reply_to_id: str | None = activity.get("replyToId")
        recipient: dict[str, Any] = activity.get("recipient") or {}
        bot_id: str | None = recipient.get("id")
        text: str = (activity.get("text") or "").strip()
        attachments: list[dict[str, Any]] = activity.get("attachments") or []

        if not text and not attachments:
            return

        tenant_id: str | None = _tenant_id_from_activity(activity)
        conv_id: str = (conversation.get("id") or "").strip()
        if not conv_id or not tenant_id:
            logger.warning("[teams_events] message missing conversation.id or tenant")
            return

        if activity.get("from", {}).get("id") == bot_id:
            return

        if _is_public_teams_channel_message(activity):
            activity_message: InboundMessage = _build_inbound_message(
                activity,
                MessageType.MENTION,
            )
            asyncio.create_task(_persist_activity(activity_message, tenant_id))
        else:
            logger.info(
                "[teams_events] Not persisting activity for conversation_type=%s conversation_id=%s",
                conversation_type,
                conv_id,
            )

        # 1:1 personal chat -> DIRECT
        # (group chats set conversationType=groupChat and/or isGroup=true)
        if not is_group and conversation_type != "groupchat":
            msg = _build_inbound_message(activity, MessageType.DIRECT)
            await TeamsMessenger().process_inbound(msg)
            return

        # Channel: @mention (no replyToId or replyToId is root) -> MENTION
        mentions_bot = _activity_mentions_bot(activity, bot_id)
        if mentions_bot:
            normalized_text = _strip_mentions(text, bot_id)
            if not normalized_text and not attachments:
                return
            lock_key = _thread_lock_manager.build_lock_key(
                tenant_id, conv_id, reply_to_id or activity.get("id")
            )
            async with _thread_lock_manager.thread_lock(lock_key):
                msg = _build_inbound_message(
                    activity, MessageType.MENTION, text_override=normalized_text
                )
                await TeamsMessenger().process_inbound(msg)
            return

        # Channel thread reply -> THREAD_REPLY
        if reply_to_id:
            lock_key = _thread_lock_manager.build_lock_key(tenant_id, conv_id, reply_to_id)
            async with _thread_lock_manager.thread_lock(lock_key):
                msg = _build_inbound_message(activity, MessageType.THREAD_REPLY)
                await TeamsMessenger().process_inbound(msg)
    except Exception as exc:
        await _record_teams_inbound_failure(activity=activity, error=exc)
        logger.exception("[teams_events] Failed to process message activity")


def _conversation_id_from_teams_activity(activity: dict[str, Any]) -> str | None:
    """Build a stable conversation identifier for Teams inbound failure metrics."""
    conversation: dict[str, Any] = activity.get("conversation") or {}
    conversation_id: str = (conversation.get("id") or "").strip()
    reply_to_id: str = (activity.get("replyToId") or activity.get("id") or "").strip()
    if conversation_id and reply_to_id:
        return f"{conversation_id}:{reply_to_id}"
    return conversation_id or None


async def _record_teams_inbound_failure(
    *,
    activity: dict[str, Any],
    error: Exception,
) -> None:
    """Record a failed Teams inbound turn when we error before turn completion."""
    from services.query_outcome_metrics import normalize_failure_reason, record_query_outcome

    try:
        await record_query_outcome(
            platform="teams",
            was_success=False,
            failure_reason=normalize_failure_reason(str(error)),
            conversation_id=_conversation_id_from_teams_activity(activity),
        )
    except Exception:
        logger.exception("[teams_events] Failed to record inbound failure metric")


@router.post("/messages", response_model=None)
async def handle_teams_messages(request: Request) -> dict[str, Any]:
    """
    Bot Framework messaging endpoint. Receives POST with Activity JSON.
    Validates JWT and processes message, conversationUpdate, installationUpdate.
    """
    await verify_teams_request(request)
    body: bytes = await request.body()
    try:
        activity: dict[str, Any] = json.loads(body.decode("utf-8"))
    except Exception as e:
        logger.error("[teams_events] Invalid JSON: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON")

    activity_type: str = (activity.get("type") or "").strip()
    activity_id: str = activity.get("id") or ""

    if activity_type == "conversationUpdate":
        return {}
    if activity_type == "installationUpdate":
        return {}

    if activity_type != "message":
        return {}

    if activity_id and await is_duplicate_activity(activity_id):
        logger.info("[teams_events] Duplicate activity: %s", activity_id)
        return {}

    asyncio.create_task(_process_message_activity(activity))
    return {}


@router.get("/messages/health")
async def teams_events_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "app_id_configured": bool(settings.MICROSOFT_APP_ID),
    }

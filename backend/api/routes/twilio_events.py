"""
Twilio SMS webhook endpoint.

Handles inbound SMS messages from Twilio:
1. Validates the X-Twilio-Signature header (HMAC-SHA1)
2. Deduplicates by MessageSid via Redis
3. Returns empty TwiML immediately (no auto-reply)
4. Processes the message in the background through the agent orchestrator
5. Replies asynchronously via the Twilio REST API (send_sms)

Security:
- All requests are verified using Twilio's HMAC-SHA1 signature scheme
- Only users with a registered phone_number can interact
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
from typing import Any
from urllib.parse import urljoin

import redis.asyncio as redis
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from config import get_redis_connection_kwargs, settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Empty TwiML response — tells Twilio not to auto-reply
_EMPTY_TWIML: str = "<Response></Response>"

# Redis client for deduplication (lazy-initialised)
_redis_client: redis.Redis | None = None


async def _get_redis() -> redis.Redis:
    """Get or create Redis client for SMS deduplication."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            settings.REDIS_URL, **get_redis_connection_kwargs()
        )
    return _redis_client


# ---------------------------------------------------------------------------
# Twilio signature validation
# ---------------------------------------------------------------------------

def verify_twilio_signature(
    url: str,
    params: dict[str, str],
    signature: str,
) -> bool:
    """
    Verify that the request came from Twilio using HMAC-SHA1.

    Twilio's algorithm:
    1. Start with the full request URL (including https scheme and any port)
    2. Sort POST parameters alphabetically by key
    3. Append each key + value to the URL string
    4. HMAC-SHA1 the result using TWILIO_AUTH_TOKEN
    5. Base64-encode and compare with X-Twilio-Signature header

    Args:
        url: The full webhook URL that Twilio was configured with
        params: The POST form parameters from the request
        signature: The X-Twilio-Signature header value

    Returns:
        True if signature is valid
    """
    auth_token: str | None = settings.TWILIO_AUTH_TOKEN
    if not auth_token:
        logger.warning("[twilio_events] TWILIO_AUTH_TOKEN not configured")
        return False

    # Build the data string: URL + sorted(key + value)
    data: str = url
    for key in sorted(params.keys()):
        data += key + params[key]

    # Compute expected signature
    mac = hmac.new(
        auth_token.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha1,
    )
    expected: str = base64.b64encode(mac.digest()).decode("utf-8")

    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

async def _is_duplicate_message(message_sid: str) -> bool:
    """
    Check if we've already processed this SMS (dedup by MessageSid).

    Twilio may retry the webhook if we don't respond quickly.
    Uses Redis NX with a 1-hour TTL.

    Args:
        message_sid: Unique Twilio message identifier

    Returns:
        True if message was already processed
    """
    if not message_sid:
        return False
    try:
        client: redis.Redis = await _get_redis()
        key: str = f"revtops:twilio_events:{message_sid}"
        was_set: bool | None = await client.set(key, "1", nx=True, ex=3600)
        return not was_set
    except Exception as e:
        logger.error("[twilio_events] Redis error during dedup: %s", e)
        # If Redis is down, process anyway (better duplicate than miss)
        return False


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

def _resolve_webhook_url(request: Request) -> str:
    """
    Return the canonical webhook URL for Twilio signature validation.

    Twilio computes its signature against the *exact* URL configured in the
    console.  Behind a reverse proxy / load-balancer the scheme, host, and
    port that FastAPI sees often differ from the public URL, causing
    signature mismatches.

    Preferred: set ``TWILIO_WEBHOOK_URL`` in env to the exact public URL
    (e.g. ``https://api.revtops.com/api/twilio/webhook``).

    Fallback: reconstruct from ``X-Forwarded-*`` headers.
    """
    configured_url: str | None = settings.TWILIO_WEBHOOK_URL
    if configured_url:
        return configured_url.rstrip("/")

    scheme: str = request.headers.get("x-forwarded-proto", request.url.scheme)
    host: str = request.headers.get(
        "x-forwarded-host",
        request.headers.get("host", request.url.hostname or "localhost"),
    )
    path: str = request.url.path
    return f"{scheme}://{host}{path}"


@router.post("/webhook", response_model=None)
async def handle_twilio_webhook(request: Request) -> Response:
    """
    Handle incoming Twilio SMS webhook.

    Twilio sends form-encoded POST with fields:
    MessageSid, AccountSid, From, To, Body, NumMedia, etc.

    We validate the signature, dedup, return empty TwiML immediately,
    and process the message in the background.
    """
    # Parse form data
    form: dict[str, str] = dict(await request.form())

    # Validate signature
    twilio_signature: str = request.headers.get("X-Twilio-Signature", "")
    webhook_url: str = _resolve_webhook_url(request)

    if not verify_twilio_signature(webhook_url, form, twilio_signature):
        logger.warning("[twilio_events] Invalid Twilio signature for %s", webhook_url)
        raise HTTPException(status_code=401, detail="Invalid signature")

    message_sid: str = form.get("MessageSid", "")
    from_number: str = form.get("From", "")
    to_number: str = form.get("To", "")
    body: str = form.get("Body", "")

    logger.info(
        "[twilio_events] Inbound SMS from=%s to=%s sid=%s body=%s",
        from_number,
        to_number,
        message_sid,
        body[:80] if body else "(empty)",
    )

    # Dedup by MessageSid
    if message_sid and await _is_duplicate_message(message_sid):
        logger.info("[twilio_events] Skipping duplicate message: %s", message_sid)
        return Response(content=_EMPTY_TWIML, media_type="application/xml")

    # Ignore empty messages
    if not body.strip():
        logger.info("[twilio_events] Ignoring empty SMS from %s", from_number)
        return Response(content=_EMPTY_TWIML, media_type="application/xml")

    # Process in background — return TwiML immediately
    asyncio.create_task(_process_inbound_sms(from_number, to_number, body, message_sid))

    return Response(content=_EMPTY_TWIML, media_type="application/xml")


async def _process_inbound_sms(
    from_number: str,
    to_number: str,
    body: str,
    message_sid: str,
) -> None:
    """Wrapper for background processing with top-level exception handling."""
    try:
        from services.sms_conversations import process_inbound_sms

        await process_inbound_sms(
            from_number=from_number,
            to_number=to_number,
            body=body,
            message_sid=message_sid,
        )
    except Exception as e:
        logger.exception("[twilio_events] Background SMS processing failed: %s", e)


@router.get("/webhook/health")
async def twilio_webhook_health() -> dict[str, Any]:
    """Health check for Twilio webhook endpoint."""
    return {
        "status": "ok",
        "twilio_configured": bool(
            settings.TWILIO_ACCOUNT_SID
            and settings.TWILIO_AUTH_TOKEN
            and settings.TWILIO_PHONE_NUMBER
        ),
    }

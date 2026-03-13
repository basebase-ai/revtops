"""
WhatsApp webhook endpoint (via Twilio).

Handles inbound WhatsApp messages from Twilio:
1. Validates the X-Twilio-Signature header (HMAC-SHA1)
2. Deduplicates by MessageSid via Redis
3. Returns empty TwiML immediately (no auto-reply)
4. Processes the message in the background through the agent orchestrator
5. Replies asynchronously via the Twilio REST API (send_sms with whatsapp=True)

Twilio uses the same Messages API for WhatsApp — the only difference is phone
numbers are prefixed with ``whatsapp:``.  We strip that prefix before passing
downstream so the rest of the pipeline works with plain E.164 numbers.

Security:
- All requests are verified using Twilio's HMAC-SHA1 signature scheme
- Only users with a registered phone_number can interact
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from api.routes.twilio_events import (
    _EMPTY_TWIML,
    _is_duplicate_message,
    verify_twilio_signature,
)
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------

def _resolve_webhook_url(request: Request) -> str:
    """
    Return the canonical webhook URL for Twilio signature validation.

    Preferred: set ``WHATSAPP_WEBHOOK_URL`` in env.
    Fallback: reconstruct from forwarded headers / request URL.
    """
    configured_url: str | None = getattr(settings, "WHATSAPP_WEBHOOK_URL", None)
    if configured_url:
        return configured_url.rstrip("/")

    scheme: str = request.headers.get("x-forwarded-proto", request.url.scheme)
    host: str = request.headers.get(
        "x-forwarded-host",
        request.headers.get("host", request.url.hostname or "localhost"),
    )
    path: str = request.url.path
    return f"{scheme}://{host}{path}"


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

def _strip_whatsapp_prefix(number: str) -> str:
    """Remove the ``whatsapp:`` prefix from a phone number if present."""
    if number.startswith("whatsapp:"):
        return number[len("whatsapp:"):]
    return number


@router.post("/webhook", response_model=None)
async def handle_whatsapp_webhook(request: Request) -> Response:
    """
    Handle incoming Twilio WhatsApp webhook.

    Twilio sends form-encoded POST with the same fields as SMS:
    MessageSid, AccountSid, From, To, Body, NumMedia, etc.
    WhatsApp numbers are prefixed with ``whatsapp:``.

    We validate the signature, dedup, return empty TwiML immediately,
    and process the message in the background.
    """
    # Parse form data
    form: dict[str, str] = dict(await request.form())

    # Validate signature
    twilio_signature: str = request.headers.get("X-Twilio-Signature", "")
    webhook_url: str = _resolve_webhook_url(request)

    if not verify_twilio_signature(webhook_url, form, twilio_signature):
        logger.warning("[whatsapp_events] Invalid Twilio signature for %s", webhook_url)
        raise HTTPException(status_code=401, detail="Invalid signature")

    message_sid: str = form.get("MessageSid", "")
    from_number: str = _strip_whatsapp_prefix(form.get("From", ""))
    to_number: str = _strip_whatsapp_prefix(form.get("To", ""))
    body: str = form.get("Body", "")

    # Extract media attachments (same MMS pattern)
    num_media: int = int(form.get("NumMedia", "0"))
    media_items: list[dict[str, str]] = []
    for i in range(num_media):
        media_url: str | None = form.get(f"MediaUrl{i}")
        media_ct: str | None = form.get(f"MediaContentType{i}")
        if media_url:
            media_items.append({"url": media_url, "content_type": media_ct or "application/octet-stream"})

    logger.info(
        "[whatsapp_events] Inbound WhatsApp from=%s to=%s sid=%s body=%s media=%d",
        from_number,
        to_number,
        message_sid,
        body[:80] if body else "(empty)",
        len(media_items),
    )

    # Dedup by MessageSid (shared with SMS — same Redis key space is fine)
    if message_sid and await _is_duplicate_message(message_sid):
        logger.info("[whatsapp_events] Skipping duplicate message: %s", message_sid)
        return Response(content=_EMPTY_TWIML, media_type="application/xml")

    # Ignore messages with no body AND no media
    if not body.strip() and not media_items:
        logger.info("[whatsapp_events] Ignoring empty WhatsApp message from %s", from_number)
        return Response(content=_EMPTY_TWIML, media_type="application/xml")

    # Process in background — return TwiML immediately
    asyncio.create_task(_process_inbound_whatsapp(from_number, to_number, body, message_sid, media_items))

    return Response(content=_EMPTY_TWIML, media_type="application/xml")


async def _process_inbound_whatsapp(
    from_number: str,
    to_number: str,
    body: str,
    message_sid: str,
    media_items: list[dict[str, str]] | None = None,
) -> None:
    """Wrapper for background processing with top-level exception handling."""
    try:
        from messengers.base import InboundMessage, MessageType
        from messengers.whatsapp import WhatsAppMessenger

        message = InboundMessage(
            external_user_id=from_number,
            text=body,
            message_type=MessageType.DIRECT,
            raw_attachments=media_items or [],
            messenger_context={"to_number": to_number},
            message_id=message_sid,
        )
        messenger = WhatsAppMessenger()
        await messenger.process_inbound(message)
    except Exception as e:
        logger.exception("[whatsapp_events] Background WhatsApp processing failed: %s", e)


@router.get("/webhook/health")
async def whatsapp_webhook_health() -> dict[str, Any]:
    """Health check for WhatsApp webhook endpoint."""
    return {
        "status": "ok",
        "twilio_configured": bool(
            settings.TWILIO_ACCOUNT_SID
            and settings.TWILIO_AUTH_TOKEN
            and settings.TWILIO_PHONE_NUMBER
        ),
    }

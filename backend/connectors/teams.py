"""
Microsoft Teams Bot Framework connector.

Provides token acquisition and REST API calls for the Bot Framework
(conversations, typing) and Microsoft Graph (user profile). Used by
the Teams messenger for sending replies and resolving user identity.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

BOT_FRAMEWORK_TOKEN_URL: str = (
    "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
)
BOT_FRAMEWORK_SCOPE: str = "https://api.botframework.com/.default"
MICROSOFT_GRAPH_API_BASE: str = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE: str = "https://graph.microsoft.com/.default"
TOKEN_CACHE_BUFFER_SECONDS: int = 300
_bot_token_cache: tuple[str, float] | None = None
_graph_token_cache: dict[str, tuple[str, float]] = {}  # tenant_id -> (token, expiry)


async def get_bot_framework_token() -> str:
    """Get a Bot Framework connector token (client credentials). Cached until near expiry."""
    global _bot_token_cache
    now: float = time.monotonic()
    if _bot_token_cache is not None and _bot_token_cache[1] > now:
        return _bot_token_cache[0]

    app_id: str | None = settings.MICROSOFT_APP_ID
    app_password: str | None = settings.MICROSOFT_APP_PASSWORD
    if not app_id or not app_password:
        raise RuntimeError(
            "MICROSOFT_APP_ID and MICROSOFT_APP_PASSWORD must be set for Teams"
        )

    async with httpx.AsyncClient() as client:
        response = await client.post(
            BOT_FRAMEWORK_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": app_id,
                "client_secret": app_password,
                "scope": BOT_FRAMEWORK_SCOPE,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15.0,
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()

    token: str = data.get("access_token", "")
    expires_in: int = int(data.get("expires_in", 3600))
    if not token:
        raise RuntimeError("Bot Framework token response missing access_token")

    _bot_token_cache = (token, now + max(0, expires_in - TOKEN_CACHE_BUFFER_SECONDS))
    return token


async def get_graph_token(tenant_id: str) -> str:
    """Get a Microsoft Graph token (client credentials) for the given tenant. Cached per tenant until near expiry."""
    global _graph_token_cache
    now: float = time.monotonic()
    cached = _graph_token_cache.get(tenant_id)
    if cached is not None and cached[1] > now:
        return cached[0]

    app_id: str | None = settings.MICROSOFT_APP_ID
    app_password: str | None = settings.MICROSOFT_APP_PASSWORD
    if not app_id or not app_password:
        raise RuntimeError(
            "MICROSOFT_APP_ID and MICROSOFT_APP_PASSWORD must be set for Teams"
        )

    token_url: str = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": app_id,
                "client_secret": app_password,
                "scope": GRAPH_SCOPE,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()

    token = data.get("access_token", "")
    expires_in = int(data.get("expires_in", 3600))
    if not token:
        raise RuntimeError("Graph token response missing access_token")

    _graph_token_cache[tenant_id] = (
        token,
        now + max(0, expires_in - TOKEN_CACHE_BUFFER_SECONDS),
    )
    return token


async def post_message(
    service_url: str,
    conversation_id: str,
    text: str,
    *,
    reply_to_id: str | None = None,
    bot_id: str | None = None,
) -> str | None:
    """
    Send a reply activity to the Bot Framework connector.

    Args:
        service_url: activity.serviceUrl from the incoming request
        conversation_id: activity.conversation.id
        text: message text
        reply_to_id: activity.replyToId for threaded replies
        bot_id: activity.recipient.id (bot's ID in the conversation)

    Returns:
        ID of the sent message, or None on failure
    """
    token: str = await get_bot_framework_token()
    app_id: str | None = settings.MICROSOFT_APP_ID
    if not app_id:
        return None

    url: str = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities"
    payload: dict[str, Any] = {
        "type": "message",
        "text": text,
        "from": {"id": bot_id or app_id, "name": "Bot"},
    }
    if reply_to_id:
        payload["replyToId"] = reply_to_id

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("id")
    except Exception as exc:
        logger.error("[teams] post_message failed: %s", exc)
        return None


async def send_typing_indicator(
    service_url: str,
    conversation_id: str,
    *,
    bot_id: str | None = None,
) -> None:
    """Send a typing activity to the conversation."""
    token = await get_bot_framework_token()
    app_id = settings.MICROSOFT_APP_ID
    if not app_id:
        return

    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities"
    payload: dict[str, Any] = {
        "type": "typing",
        "from": {"id": bot_id or app_id, "name": "Bot"},
    }

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
    except Exception as exc:
        logger.debug("[teams] send_typing_indicator failed: %s", exc)


async def get_user_info(tenant_id: str, user_aad_id: str) -> dict[str, Any] | None:
    """
    Fetch user profile from Microsoft Graph (requires User.Read.All application permission).

    Args:
        tenant_id: Azure AD tenant ID
        user_aad_id: User's Azure AD object ID (activity.from.aadObjectId or activity.from.id)

    Returns:
        Graph user resource dict (mail, displayName, etc.) or None
    """
    try:
        token = await get_graph_token(tenant_id)
        url = f"{MICROSOFT_GRAPH_API_BASE}/users/{user_aad_id}"
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                params={"$select": "id,mail,userPrincipalName,displayName"},
                timeout=10.0,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        logger.warning("[teams] get_user_info failed for %s: %s", user_aad_id, exc)
        return None


async def download_teams_file(
    download_url: str,
    *,
    bot_token: str | None = None,
) -> tuple[bytes, str, str] | None:
    """
    Download a file from a Teams attachment (contentUrl with auth).

    If contentUrl requires auth, the Bot Framework token may be used;
    some URLs are pre-authenticated. Returns (data, filename, content_type) or None.
    """
    token: str = bot_token or await get_bot_framework_token()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                download_url,
                headers={"Authorization": f"Bearer {token}"},
                follow_redirects=True,
                timeout=30.0,
            )
            response.raise_for_status()
            data: bytes = response.content
            content_type: str = (
                response.headers.get("content-type") or "application/octet-stream"
            )
            filename: str = "teams_file"
            cd: str | None = response.headers.get("content-disposition")
            if cd and "filename=" in cd:
                part = cd.split("filename=", 1)[1].strip().strip('"')
                if part:
                    filename = part
            return (data, filename, content_type)
    except Exception as exc:
        logger.error("[teams] download_teams_file failed: %s", exc)
        return None

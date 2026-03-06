"""
SMS service for sending text messages.

Uses Twilio for SMS delivery. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
and TWILIO_PHONE_NUMBER in environment.
"""
from __future__ import annotations

import base64
from typing import Optional
from urllib.parse import urlencode

import httpx

from config import settings


async def send_sms(
    to: str,
    body: str,
    from_number: Optional[str] = None,
    media_urls: Optional[list[str]] = None,
    whatsapp: bool = False,
) -> dict[str, str | bool]:
    """
    Send an SMS (or MMS with media) via Twilio.

    Args:
        to: Recipient phone number (E.164 format, e.g., +14155551234)
        body: Message text (max 1600 characters)
        from_number: Optional from number (defaults to TWILIO_PHONE_NUMBER)
        media_urls: Optional list of public URLs for MMS media (up to 10)

    Returns:
        Dict with status, message_sid on success, or error on failure
    """
    account_sid = settings.TWILIO_ACCOUNT_SID if hasattr(settings, 'TWILIO_ACCOUNT_SID') else None
    auth_token = settings.TWILIO_AUTH_TOKEN if hasattr(settings, 'TWILIO_AUTH_TOKEN') else None
    default_from = settings.TWILIO_PHONE_NUMBER if hasattr(settings, 'TWILIO_PHONE_NUMBER') else None
    
    if not account_sid or not auth_token:
        print(f"[SMS] Twilio not configured, skipping SMS to {to}")
        return {
            "success": False,
            "error": "Twilio not configured. Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN.",
        }
    
    from_phone = from_number or default_from
    if not from_phone:
        return {
            "success": False,
            "error": "No from number specified and TWILIO_PHONE_NUMBER not set.",
        }
    
    # Truncate body if too long
    if len(body) > 1600:
        body = body[:1597] + "..."
    
    # Build auth header
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
    
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    
    async with httpx.AsyncClient() as client:
        try:
            # Build form params — use list of tuples + urlencode(doseq=True)
            # so we can repeat the MediaUrl key for multiple MMS attachments
            to_value: str = f"whatsapp:{to}" if whatsapp else to
            from_value: str = f"whatsapp:{from_phone}" if whatsapp else from_phone
            params: list[tuple[str, str]] = [
                ("To", to_value),
                ("From", from_value),
                ("Body", body),
            ]
            # Twilio accepts up to 10 repeated MediaUrl params for MMS
            if media_urls:
                for murl in media_urls[:10]:
                    params.append(("MediaUrl", murl))

            response = await client.post(
                url,
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                content=urlencode(params, doseq=True),
                timeout=10.0,
            )
            
            if response.status_code in (200, 201):
                data = response.json()
                print(f"[SMS] Sent to {to}: {data.get('sid')}")
                return {
                    "success": True,
                    "message_sid": data.get("sid"),
                    "status": data.get("status"),
                }
            else:
                error_data = response.json()
                error_msg = error_data.get("message", response.text)
                print(f"[SMS] Failed to send to {to}: {error_msg}")
                return {
                    "success": False,
                    "error": error_msg,
                }
                
        except Exception as e:
            print(f"[SMS] Error sending to {to}: {e}")
            return {
                "success": False,
                "error": str(e),
            }

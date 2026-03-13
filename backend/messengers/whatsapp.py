"""WhatsApp messenger — thin subclass of :class:`TwilioPhoneMessenger`."""
from __future__ import annotations

from messengers._twilio_phone import TwilioPhoneMessenger
from messengers.base import MessengerMeta, ResponseMode


class WhatsAppMessenger(TwilioPhoneMessenger):
    meta = MessengerMeta(
        name="WhatsApp",
        slug="whatsapp",
        response_mode=ResponseMode.BATCH,
        description="WhatsApp via Twilio",
    )

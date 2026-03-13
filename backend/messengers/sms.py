"""SMS messenger — thin subclass of :class:`TwilioPhoneMessenger`."""
from __future__ import annotations

from messengers._twilio_phone import TwilioPhoneMessenger
from messengers.base import MessengerMeta, ResponseMode


class SmsMessenger(TwilioPhoneMessenger):
    meta = MessengerMeta(
        name="SMS",
        slug="sms",
        response_mode=ResponseMode.BATCH,
        description="SMS/MMS via Twilio",
    )

from __future__ import annotations

from messengers._twilio_phone import TwilioPhoneMessenger
from messengers._workspace import WorkspaceMessenger
from messengers.registry import discover_messengers


_ALLOWED_BASES: tuple[type, ...] = (TwilioPhoneMessenger, WorkspaceMessenger)


def test_all_message_delivery_messengers_inherit_stream_break_aware_base() -> None:
    """Guardrail: delivery messengers must inherit a base that uses central safe breaks.

    Current stream break behavior is centralized in:
    - ``TwilioPhoneMessenger`` (batch delivery via ``_split_text``)
    - ``WorkspaceMessenger`` (streaming flush boundaries)

    Any newly added messenger type that delivers responses should inherit one
    of these bases; otherwise this test fails and prompts explicit adoption.
    """

    registry = discover_messengers()

    for slug, messenger_cls in registry.items():
        if slug == "web":
            # Web chat uses a separate websocket path (no messenger chunking).
            continue

        assert issubclass(messenger_cls, _ALLOWED_BASES), (
            f"Messenger '{slug}' must inherit TwilioPhoneMessenger or "
            "WorkspaceMessenger so it uses central stream break handling."
        )

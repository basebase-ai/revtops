"""
Messenger framework: pluggable chat messenger integrations.

Mirrors the connector pattern (``backend/connectors/``) but for
bidirectional chat messengers (Slack, SMS, WhatsApp, web, etc.).

Use ``discover_messengers()`` to get all registered messenger classes.
"""
from messengers.base import (
    BaseMessenger,
    InboundMessage,
    MessageType,
    MessengerMeta,
    OutboundResponse,
    ResponseMode,
)
from messengers.registry import discover_messengers

__all__ = [
    "BaseMessenger",
    "InboundMessage",
    "MessageType",
    "MessengerMeta",
    "OutboundResponse",
    "ResponseMode",
    "discover_messengers",
]

"""
Web messenger — registration stub for the WebSocket-based web chat.

The web chat interface uses a fundamentally different delivery path
(WebSocket push via ``TaskManager``) so ``process_inbound`` is not used.
This class exists so that ``discover_messengers()`` returns ``"web"`` as
a registered messenger and ``ConversationSource`` stays consistent.
"""
from __future__ import annotations

from typing import Any

from messengers.base import (
    BaseMessenger,
    InboundMessage,
    MessengerMeta,
    OutboundResponse,
    ResponseMode,
)
from models.conversation import Conversation
from models.user import User


class WebMessenger(BaseMessenger):
    meta = MessengerMeta(
        name="Web",
        slug="web",
        response_mode=ResponseMode.STREAMING,
        description="Web chat via WebSocket",
    )

    async def resolve_organization(
        self, user: User, message: InboundMessage,
    ) -> tuple[str, str] | None:
        raise NotImplementedError("Web messenger uses WebSocket flow, not process_inbound()")

    async def find_or_create_conversation(
        self, organization_id: str, user: User, message: InboundMessage,
    ) -> Conversation:
        raise NotImplementedError("Web messenger uses WebSocket flow, not process_inbound()")

    async def download_attachments(self, message: InboundMessage) -> list[str]:
        return []

    async def send_response(
        self, message: InboundMessage, response: OutboundResponse,
    ) -> None:
        raise NotImplementedError("Web messenger uses WebSocket flow, not process_inbound()")

    def format_text(self, markdown: str) -> str:
        return markdown

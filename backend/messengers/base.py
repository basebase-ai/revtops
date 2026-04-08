"""
Base messenger class and shared data types.

Every chat messenger (Slack, SMS, WhatsApp, web, MS Teams, Discord, ...)
inherits from :class:`BaseMessenger` and declares a class-level ``meta``
attribute of type :class:`MessengerMeta` so that :func:`discover_messengers`
can auto-discover it.

``BaseMessenger.process_inbound`` implements the common pipeline:

    resolve user -> resolve org -> check credits -> find/create conversation
    -> download attachments -> run orchestrator -> format -> deliver response

Subclasses only override the abstract hooks that differ per platform.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from models.user import User
from services.anthropic_health import user_message_for_agent_stream_failure

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ResponseMode(Enum):
    """How the messenger delivers responses back to the user."""

    STREAMING = "streaming"
    BATCH = "batch"


class MessageType(Enum):
    """Classification of an inbound message."""

    DIRECT = "direct"
    MENTION = "mention"
    THREAD_REPLY = "thread_reply"


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MessengerMeta:
    """Self-describing metadata attached to every messenger class."""

    name: str
    slug: str
    response_mode: ResponseMode
    description: str = ""


# ---------------------------------------------------------------------------
# Message data classes
# ---------------------------------------------------------------------------


@dataclass
class InboundMessage:
    """Platform-normalized inbound message."""

    external_user_id: str
    text: str
    message_type: MessageType
    raw_attachments: list[dict[str, Any]] = field(default_factory=list)
    messenger_context: dict[str, Any] = field(default_factory=dict)
    message_id: str = ""
    # Structured at-mentions from the platform (e.g. Slack <@U…>).
    mentions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class OutboundResponse:
    """Platform-normalized outbound response."""

    text: str
    media_urls: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseMessenger(ABC):
    """Abstract base for all chat messenger integrations.

    Subclasses **must** set a class-level ``meta: MessengerMeta`` attribute.

    The concrete :meth:`process_inbound` method runs the shared pipeline;
    subclasses implement the abstract hooks below to supply platform-specific
    behaviour.
    """

    meta: ClassVar[MessengerMeta]

    # ------------------------------------------------------------------
    # Identity resolution
    # ------------------------------------------------------------------

    async def resolve_user(self, message: InboundMessage) -> User | None:
        """Map an external messenger identity to a RevTops :class:`User`.

        Default implementation queries ``messenger_user_mappings`` by
        ``(platform, external_user_id)``.  When a ``workspace_id`` is
        present in the message context, an exact match is preferred but
        rows with ``workspace_id IS NULL`` (legacy data) are accepted as
        a fallback.  Subclasses may override to add strategies such as
        phone-number lookup.
        """
        from models.messenger_user_mapping import MessengerUserMapping
        from models.database import get_admin_session
        from sqlalchemy import case, or_, select

        platform: str = self.meta.slug
        external_id: str = message.external_user_id
        workspace_id: str | None = message.messenger_context.get("workspace_id")

        async with get_admin_session() as session:
            stmt = (
                select(User, MessengerUserMapping.workspace_id)
                .join(
                    MessengerUserMapping,
                    MessengerUserMapping.user_id == User.id,
                )
                .where(MessengerUserMapping.platform == platform)
                .where(MessengerUserMapping.external_user_id == external_id)
            )
            if workspace_id is not None:
                stmt = stmt.where(
                    or_(
                        MessengerUserMapping.workspace_id == workspace_id,
                        MessengerUserMapping.workspace_id.is_(None),
                    )
                ).order_by(
                    case(
                        (MessengerUserMapping.workspace_id == workspace_id, 0),
                        else_=1,
                    )
                )
            rows = (await session.execute(stmt)).first()
            return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Organisation resolution
    # ------------------------------------------------------------------

    @abstractmethod
    async def resolve_organization(
        self,
        user: User,
        message: InboundMessage,
    ) -> tuple[str, str] | None:
        """Return ``(organization_id, organization_name)`` or *None*.

        Returning *None* means a qualifying question has been sent and the
        current message should not be processed further.
        """

    # ------------------------------------------------------------------
    # Conversation management
    # ------------------------------------------------------------------

    @abstractmethod
    async def find_or_create_conversation(
        self,
        organization_id: str,
        user: User,
        message: InboundMessage,
    ) -> str:
        """Find or create the conversation and return its UUID string."""

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    @abstractmethod
    async def download_attachments(
        self,
        message: InboundMessage,
    ) -> list[str]:
        """Download media and return a list of ``upload_id`` strings."""

    # ------------------------------------------------------------------
    # Response delivery
    # ------------------------------------------------------------------

    @abstractmethod
    async def send_response(
        self,
        message: InboundMessage,
        response: OutboundResponse,
    ) -> None:
        """Deliver *response* back to the user on this messenger."""

    # ------------------------------------------------------------------
    # Text formatting
    # ------------------------------------------------------------------

    @abstractmethod
    def format_text(self, markdown: str) -> str:
        """Convert Markdown to the messenger's native text format."""

    # ------------------------------------------------------------------
    # Customisable messages
    # ------------------------------------------------------------------

    def unknown_user_message(self) -> str:
        """Reply text when the sender cannot be resolved to a user."""
        return (
            "This identity is not registered with Basebase. "
            "Please link your account first."
        )

    def no_credits_message(self) -> str:
        """Reply text when the organisation is out of credits."""
        return (
            "You're out of credits or don't have an active subscription. "
            "Please add a payment method in Basebase to continue."
        )

    # ------------------------------------------------------------------
    # Shared pipeline
    # ------------------------------------------------------------------

    async def process_inbound(
        self,
        message: InboundMessage,
    ) -> dict[str, Any]:
        """End-to-end pipeline for an inbound message.

        1. Resolve user
        2. Resolve organisation
        3. Check credits
        4. Find / create conversation
        5. Download attachments
        6. Run :class:`ChatOrchestrator`
        7. Format and deliver response

        Returns a result dict with ``status`` and supplementary fields.
        """
        from agents.orchestrator import ChatOrchestrator
        from services.credits import can_use_credits

        result: dict[str, Any] | None = None
        caught_error: Exception | None = None
        try:
            # 1. Resolve user
            user: User | None = await self.resolve_user(message)
            if user is None:
                logger.info(
                    "[%s] Unknown user external_id=%s",
                    self.meta.slug,
                    message.external_user_id,
                )
                await self.send_response(
                    message,
                    OutboundResponse(text=self.unknown_user_message()),
                )
                result = {"status": "rejected", "reason": "unknown_user"}
                return result

            user_id: str = str(user.id)
            user_email: str | None = user.email

            # 2. Resolve organisation
            org_result: tuple[str, str] | None = await self.resolve_organization(
                user, message
            )
            if org_result is None:
                result = {"status": "pending_org_choice"}
                return result

            organization_id: str
            organization_name: str
            organization_id, organization_name = org_result

            logger.info(
                "[%s] Resolved org=%s (%s) for user=%s",
                self.meta.slug,
                organization_id,
                organization_name,
                user_id,
            )

            # 3. Check credits
            if not await can_use_credits(organization_id):
                await self.send_response(
                    message,
                    OutboundResponse(text=self.no_credits_message()),
                )
                result = {"status": "error", "error": "insufficient_credits"}
                return result

            # 4. Find / create conversation
            conversation_id: str = await self.find_or_create_conversation(
                organization_id, user, message
            )

            # 5. Download attachments
            attachment_ids: list[str] = await self.download_attachments(message)

            message_text: str = message.text or (
                "(see attached files)" if attachment_ids else ""
            )

            if not message_text.strip():
                result = {"status": "ok", "reason": "empty_after_org_selection"}
                return result

            # 6. Run orchestrator
            orchestrator = ChatOrchestrator(
                user_id=user_id,
                organization_id=organization_id,
                conversation_id=conversation_id,
                user_email=user_email,
                source_user_id=message.external_user_id,
                source_user_email=user_email,
                workflow_context=None,
                source=self.meta.slug,
            )

            full_response: str = ""
            outbound_media_urls: list[str] = []
            try:
                async for chunk in orchestrator.process_message(
                    message_text,
                    attachment_ids=attachment_ids or None,
                ):
                    if chunk.startswith("{"):
                        outbound_media_urls.extend(
                            self._extract_media_from_chunk(chunk)
                        )
                    else:
                        full_response += chunk
            except Exception as exc:
                logger.exception(
                    "[%s] Orchestrator error conversation=%s",
                    self.meta.slug,
                    conversation_id,
                )
                full_response += user_message_for_agent_stream_failure(exc)

            # 7. Format and deliver
            response_text: str = self.format_text(full_response.strip())
            if response_text or outbound_media_urls:
                await self.send_response(
                    message,
                    OutboundResponse(
                        text=response_text,
                        media_urls=outbound_media_urls,
                    ),
                )
            else:
                logger.warning(
                    "[%s] Empty response for conversation=%s",
                    self.meta.slug,
                    conversation_id,
                )

            logger.info(
                "[%s] Replied (%d chars) conversation=%s",
                self.meta.slug,
                len(response_text),
                conversation_id,
            )
            result = {
                "status": "success",
                "conversation_id": conversation_id,
                "response_length": len(response_text),
            }
            return result
        except Exception as exc:
            caught_error = exc
            raise
        finally:
            await self._record_query_outcome(result=result, error=caught_error)

    async def _record_query_outcome(
        self,
        *,
        result: dict[str, Any] | None,
        error: Exception | None,
    ) -> None:
        """Persist query success/failure for rolling monitoring windows."""
        from services.query_outcome_metrics import record_query_outcome

        was_success = self._is_successful_query_outcome(result=result, error=error)
        try:
            await record_query_outcome(platform=self.meta.slug, was_success=was_success)
        except Exception:
            logger.exception(
                "[%s] Failed to record query outcome was_success=%s result=%s",
                self.meta.slug,
                was_success,
                result,
            )

    @staticmethod
    def _is_successful_query_outcome(
        *,
        result: dict[str, Any] | None,
        error: Exception | None,
    ) -> bool:
        """Classify whether a completed inbound query should count as success."""
        if error is not None:
            return False
        if not result:
            return False
        status = result.get("status")
        if status == "success":
            return True
        if status == "rejected" and result.get("reason") == "unknown_user":
            return True
        if status == "error" and result.get("error") == "insufficient_credits":
            return True
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_media_from_chunk(chunk: str) -> list[str]:
        """Extract public media URLs from a JSON orchestrator chunk.

        Override in subclasses that support rich media (e.g. MMS images).
        """
        return []

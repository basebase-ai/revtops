from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.conversation import Conversation


class ContentGroup(Base):
    __tablename__ = "content_groups"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "platform",
            "workspace_id",
            "external_group_id",
            "external_thread_id",
            name="uq_content_groups_key",
        ),
        Index("ix_content_groups_org_platform_workspace", "organization_id", "platform", "workspace_id"),
        Index("ix_content_groups_org_platform_group", "organization_id", "platform", "external_group_id"),
        Index(
            "uq_content_groups_channel_non_thread",
            "organization_id",
            "platform",
            "workspace_id",
            "external_group_id",
            unique=True,
            postgresql_where=text("external_thread_id IS NULL"),
        ),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(100), nullable=False)
    external_group_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_thread_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ContentGroupSummary(Base):
    __tablename__ = "content_group_summaries"
    __table_args__ = (
        Index(
            "ix_group_summaries_group_through_desc",
            "content_group_id",
            text("summarized_through_at DESC"),
        ),
        Index(
            "ix_group_summaries_org_group_range",
            "organization_id",
            "content_group_id",
            "first_message_at",
            "last_message_at",
        ),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    content_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_groups.id", ondelete="CASCADE"), nullable=False
    )
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    first_message_external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_message_external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    summarized_through_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str] = mapped_column(
        String(50), nullable=False, default="v1", server_default=text("'v1'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Campfire(Base):
    __tablename__ = "campfires"
    __table_args__ = (
        Index("ix_campfires_org_archived", "organization_id", "is_archived"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CampfireContentGroup(Base):
    __tablename__ = "campfire_content_groups"
    __table_args__ = (
        UniqueConstraint("campfire_id", "content_group_id", name="uq_campfire_content_group"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campfire_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campfires.id", ondelete="CASCADE"), nullable=False
    )
    content_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_groups.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CampfireConversation(Base):
    __tablename__ = "campfire_conversations"
    __table_args__ = (
        UniqueConstraint("campfire_id", "conversation_id", name="uq_campfire_conversation"),
        {"extend_existing": True},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campfire_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campfires.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    added_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

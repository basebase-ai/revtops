"""
Pipeline models - normalized representation of CRM pipelines/sales processes.

Works for both HubSpot (Pipelines) and Salesforce (Sales Processes/Record Types).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.deal import Deal
    from models.organization import Organization


class Pipeline(Base):
    """Pipeline definition (HubSpot Pipeline or Salesforce Sales Process)."""

    __tablename__ = "pipelines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    source_system: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_order: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    stages: Mapped[list["PipelineStage"]] = relationship(
        "PipelineStage",
        back_populates="pipeline",
        cascade="all, delete-orphan",
        order_by="PipelineStage.display_order",
    )
    deals: Mapped[list["Deal"]] = relationship("Deal", back_populates="pipeline")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "name": self.name,
            "display_order": self.display_order,
            "is_default": self.is_default,
            "source_system": self.source_system,
            "source_id": self.source_id,
            "stages": [s.to_dict() for s in self.stages],
        }


class PipelineStage(Base):
    """Stage within a pipeline."""

    __tablename__ = "pipeline_stages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_order: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    probability: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_closed_won: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_closed_lost: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    pipeline: Mapped["Pipeline"] = relationship("Pipeline", back_populates="stages")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "name": self.name,
            "display_order": self.display_order,
            "probability": self.probability,
            "is_closed_won": self.is_closed_won,
            "is_closed_lost": self.is_closed_lost,
            "source_id": self.source_id,
        }

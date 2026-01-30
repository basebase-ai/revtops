"""
SheetImport model for tracking Google Sheets import jobs.

Records the state of import operations including configuration,
results, and any errors encountered.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import relationship

from models.database import Base


class SheetImport(Base):
    """
    Tracks a Google Sheets import job.
    
    Lifecycle:
    1. pending - Import created, waiting for user to confirm
    2. processing - Import in progress
    3. completed - Import finished successfully
    4. failed - Import failed with error
    5. cancelled - User cancelled the import
    """
    
    __tablename__ = "sheet_imports"
    
    id: UUID = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: UUID = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: UUID = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    
    # Spreadsheet info
    spreadsheet_id: str = Column(String(255), nullable=False)
    spreadsheet_name: Optional[str] = Column(String(500), nullable=True)
    
    # Import configuration
    # Structure: {"tab_mappings": [{"tab_name": "...", "entity_type": "...", "column_mappings": {...}}]}
    config: dict[str, Any] = Column(JSONB, nullable=False, default=dict)
    
    # Status tracking
    status: str = Column(
        String(50),
        nullable=False,
        default="pending",
        index=True,
    )  # pending, processing, completed, failed, cancelled
    
    # Results
    # Structure: {"created": X, "updated": Y, "skipped": Z, "total_errors": N}
    results: Optional[dict[str, Any]] = Column(JSONB, nullable=True)
    
    # Errors (first 100 errors stored)
    # Structure: [{"tab": "...", "row": N, "error": "..."}]
    errors: Optional[list[dict[str, Any]]] = Column(JSONB, nullable=True)
    
    # Error message for failed imports
    error_message: Optional[str] = Column(Text, nullable=True)
    
    # Timestamps
    created_at: datetime = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    started_at: Optional[datetime] = Column(DateTime, nullable=True)
    completed_at: Optional[datetime] = Column(DateTime, nullable=True)
    
    # Relationships
    organization = relationship("Organization", back_populates="sheet_imports")
    user = relationship("User", back_populates="sheet_imports")
    
    __table_args__ = (
        Index("ix_sheet_imports_org_status", "organization_id", "status"),
    )
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "organization_id": str(self.organization_id),
            "user_id": str(self.user_id) if self.user_id else None,
            "spreadsheet_id": self.spreadsheet_id,
            "spreadsheet_name": self.spreadsheet_name,
            "config": self.config,
            "status": self.status,
            "results": self.results,
            "errors": self.errors,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "started_at": self.started_at.isoformat() + "Z" if self.started_at else None,
            "completed_at": self.completed_at.isoformat() + "Z" if self.completed_at else None,
        }

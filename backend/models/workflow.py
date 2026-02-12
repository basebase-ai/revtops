"""
Workflow models for user-defined automations.

Workflows allow users to create automated actions that:
- Run on a schedule (cron-based)
- Trigger on events (sync completed, deal created, etc.)
- Execute a series of steps (query, LLM, notify, etc.)

In the unified architecture, workflows are "scheduled prompts to the agent"
and their execution is visible as a conversation.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.database import Base

if TYPE_CHECKING:
    from models.conversation import Conversation


class Workflow(Base):
    """
    Workflow definition model.
    
    A workflow defines:
    - What triggers it (schedule or event)
    - What steps to execute
    - Where to send output
    """

    __tablename__ = "workflows"
    __table_args__ = (
        Index("ix_workflows_org_enabled", "organization_id", "is_enabled"),
        Index("ix_workflows_trigger_type", "trigger_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", onupdate="CASCADE"), nullable=False
    )

    # Workflow metadata
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Trigger configuration
    # trigger_type: 'schedule' | 'event' | 'manual'
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False)
    
    # trigger_config examples:
    # For schedule: { "cron": "0 8 * * *" }  (8 AM daily)
    # For event: { "event": "sync.completed", "filter": { "provider": "hubspot" } }
    # For manual: {} (triggered via API)
    trigger_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    # Legacy: Workflow steps - ordered list of actions to execute
    # Deprecated in favor of prompt-based execution
    # Kept for backward compatibility with existing workflows
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    
    # NEW: Natural language prompt for the agent
    # This replaces the rigid step definitions with flexible agent instructions
    # Example: "Query deals that haven't had activity in 30 days, summarize the 
    # top 5 at-risk deals, and post to #sales-alerts on Slack"
    prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # NEW: Tools pre-approved for this workflow (no user approval needed)
    # Example: ["send_slack"] means agent can post to Slack without approval
    # for THIS workflow only
    auto_approve_tools: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    # Explicit workflow permissions that must be granted before certain tools
    # can run without approval. Example: ["github_issues_write"]
    auto_approve_permissions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    
    # NEW: Typed input/output schemas for workflow composition
    # input_schema: JSON Schema defining expected input parameters
    # - null (default) = accepts any trigger_data, no validation
    # - When defined: validates input, injects typed params into prompt
    input_schema: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    
    # output_schema: JSON Schema defining expected output format
    # - null (default) = string/free-form response from agent
    # - When defined: attempts to extract structured output
    output_schema: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    
    # child_workflows: List of workflow IDs this workflow can call
    # At runtime, these are resolved to full metadata and injected into the prompt
    # so the agent knows what workflows are available without needing to look them up
    child_workflows: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    # Output configuration (optional, can also be in last step)
    # Example: { "channel": "email", "to": "user@example.com" }
    output_config: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )

    # Status
    is_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    runs: Mapped[list["WorkflowRun"]] = relationship(
        "WorkflowRun", back_populates="workflow", lazy="dynamic",
        cascade="all, delete-orphan", passive_deletes=True
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation", back_populates="workflow", lazy="dynamic"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "organization_id": str(self.organization_id),
            "created_by_user_id": str(self.created_by_user_id),
            "name": self.name,
            "description": self.description,
            "trigger_type": self.trigger_type,
            "trigger_config": self.trigger_config,
            "steps": self.steps,
            "prompt": self.prompt,
            "auto_approve_tools": self.auto_approve_tools,
            "auto_approve_permissions": self.auto_approve_permissions,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "child_workflows": self.child_workflows,
            "output_config": self.output_config,
            "is_enabled": self.is_enabled,
            "last_run_at": f"{self.last_run_at.isoformat()}Z" if self.last_run_at else None,
            "last_error": self.last_error,
            "created_at": f"{self.created_at.isoformat()}Z",
            "updated_at": f"{self.updated_at.isoformat()}Z",
        }
    
    @property
    def has_prompt(self) -> bool:
        """Check if this workflow uses the new prompt-based execution."""
        return bool(self.prompt and self.prompt.strip())


class WorkflowRun(Base):
    """
    Workflow execution log.
    
    Records each execution of a workflow including:
    - What triggered it
    - Step-by-step results
    - Any errors
    """

    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("ix_workflow_runs_workflow_id", "workflow_id"),
        Index("ix_workflow_runs_org_status", "organization_id", "status"),
        Index("ix_workflow_runs_started_at", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )

    # What triggered this run
    # Examples: 'schedule', 'event:sync.completed', 'manual', 'api'
    triggered_by: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # Data from the trigger (e.g., event payload)
    trigger_data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Execution status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")

    # Step execution results
    # Example:
    # [
    #   { "step_index": 0, "action": "query", "result": {...}, "duration_ms": 150 },
    #   { "step_index": 1, "action": "llm", "result": {...}, "duration_ms": 2500 },
    # ]
    steps_completed: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )

    # Final output (what was sent/returned)
    output: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Error information
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    workflow: Mapped["Workflow"] = relationship("Workflow", back_populates="runs")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "workflow_id": str(self.workflow_id),
            "organization_id": str(self.organization_id),
            "triggered_by": self.triggered_by,
            "trigger_data": self.trigger_data,
            "status": self.status,
            "steps_completed": self.steps_completed,
            "output": self.output,
            "error_message": self.error_message,
            "started_at": f"{self.started_at.isoformat()}Z",
            "completed_at": f"{self.completed_at.isoformat()}Z" if self.completed_at else None,
            "duration_ms": (
                int((self.completed_at - self.started_at).total_seconds() * 1000)
                if self.completed_at else None
            ),
        }

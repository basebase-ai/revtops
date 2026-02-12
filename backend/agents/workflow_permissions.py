"""Workflow-scoped auto-approval permissions.

These permissions are explicit toggles that gate whether certain sensitive
tools can run without approval during workflow execution.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowPermissionDefinition:
    """Describes a workflow permission and the tools it controls."""

    key: str
    label: str
    description: str
    tool_names: tuple[str, ...]


GITHUB_ISSUES_WRITE_PERMISSION = "github_issues_write"


WORKFLOW_PERMISSION_DEFINITIONS: dict[str, WorkflowPermissionDefinition] = {
    GITHUB_ISSUES_WRITE_PERMISSION: WorkflowPermissionDefinition(
        key=GITHUB_ISSUES_WRITE_PERMISSION,
        label="GitHub issues and comments",
        description=(
            "Allows workflows to create/update GitHub issues and issue comments "
            "without pausing for approval. Does not grant code write access."
        ),
        tool_names=("create_github_issue", "create_github_issue_comment"),
    ),
}


TOOL_TO_REQUIRED_WORKFLOW_PERMISSION: dict[str, str] = {
    tool_name: permission.key
    for permission in WORKFLOW_PERMISSION_DEFINITIONS.values()
    for tool_name in permission.tool_names
}


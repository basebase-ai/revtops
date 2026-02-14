from datetime import datetime
from types import SimpleNamespace

from workers.tasks.workflows import (
    build_workflow_runtime_context,
    format_workflow_runtime_context_for_prompt,
)


def test_build_runtime_context_uses_manual_user_invoker_and_last_run() -> None:
    workflow = SimpleNamespace(
        id="wf-123",
        created_by_user_id="user-456",
        last_run_at=datetime(2025, 1, 2, 3, 4, 5),
    )
    run = SimpleNamespace(id="run-789")

    context = build_workflow_runtime_context(
        workflow=workflow,
        run=run,
        triggered_by="manual",
        trigger_data=None,
        execution_started_at=datetime(2026, 2, 3, 4, 5, 6),
    )

    assert context["workflow_id"] == "wf-123"
    assert context["run_id"] == "run-789"
    assert context["invoked_by"] == "user:user-456"
    assert context["execution_started_at"] == "2026-02-03T04:05:06Z"
    assert context["last_run_at"] == "2025-01-02T03:04:05Z"
    assert context["current_datetime"] is not None


def test_build_runtime_context_prefers_parent_workflow_invoker() -> None:
    workflow = SimpleNamespace(id="wf-child", created_by_user_id="user-456", last_run_at=None)
    run = SimpleNamespace(id="run-child")

    context = build_workflow_runtime_context(
        workflow=workflow,
        run=run,
        triggered_by="run_workflow",
        trigger_data={"_parent_context": {"parent_workflow_id": "wf-parent"}},
        execution_started_at=datetime(2026, 2, 3, 4, 5, 6),
    )

    assert context["invoked_by"] == "workflow:wf-parent"
    assert context["last_run_at"] is None


def test_format_runtime_context_for_prompt_includes_required_fields() -> None:
    prompt_context = format_workflow_runtime_context_for_prompt(
        {
            "workflow_id": "wf-123",
            "invoked_by": "process:schedule",
            "current_datetime": "2026-02-03T04:05:06Z",
            "execution_started_at": "2026-02-03T04:04:00Z",
            "last_run_at": None,
            "run_id": "run-123",
        }
    )

    assert "Current workflow ID: wf-123" in prompt_context
    assert "Invoked by: process:schedule" in prompt_context
    assert "Current date/time (UTC): 2026-02-03T04:05:06Z" in prompt_context
    assert "Last run time (UTC): never" in prompt_context


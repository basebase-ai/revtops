from datetime import datetime

from workers.tasks.workflows import (
    build_workflow_runtime_context,
    format_workflow_runtime_context_for_prompt,
)


def test_build_workflow_runtime_context_includes_required_fields() -> None:
    started = datetime(2026, 1, 15, 13, 45, 22)
    previous = datetime(2026, 1, 14, 7, 0, 0)

    context = build_workflow_runtime_context(
        workflow_id="wf-123",
        triggered_by="run_workflow",
        started_at=started,
        last_run_at=previous,
    )

    assert context == {
        "workflow_id": "wf-123",
        "invoked_by": "run_workflow",
        "current_datetime": "2026-01-15T13:45:22Z",
        "last_run_at": "2026-01-14T07:00:00Z",
    }


def test_format_workflow_runtime_context_for_prompt_handles_never_last_run() -> None:
    context = build_workflow_runtime_context(
        workflow_id="wf-999",
        triggered_by="manual",
        started_at=datetime(2026, 2, 1, 0, 0, 0),
        last_run_at=None,
    )

    rendered = format_workflow_runtime_context_for_prompt(context)

    assert "Current workflow ID: wf-999" in rendered
    assert "Invoked by: manual" in rendered
    assert "Current date/time (UTC): 2026-02-01T00:00:00Z" in rendered
    assert "Last run time (UTC): never" in rendered

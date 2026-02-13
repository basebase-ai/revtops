from workers.tasks.workflows import (
    WORKFLOW_NESTING_GUARDRAIL,
    format_child_workflows_for_prompt,
)


def test_child_workflow_prompt_marks_usage_as_explicit_only() -> None:
    prompt_text = format_child_workflows_for_prompt(
        [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "name": "Example Child",
                "description": "Runs a specialist enrichment flow",
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": {"type": "object", "properties": {}},
            }
        ]
    )

    assert prompt_text is not None
    assert "only use run_workflow or loop_over when explicitly requested" in prompt_text
    assert "for small prompts or brief tasks, prefer completing the work directly" in prompt_text


def test_workflow_nesting_guardrail_mentions_explicit_requests() -> None:
    assert "Do NOT create or invoke child workflows" in WORKFLOW_NESTING_GUARDRAIL
    assert "explicitly asks" in WORKFLOW_NESTING_GUARDRAIL
    assert "at 5 or fewer" in WORKFLOW_NESTING_GUARDRAIL

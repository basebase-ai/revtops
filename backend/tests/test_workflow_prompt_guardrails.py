from workers.tasks.workflows import (
    WORKFLOW_NESTING_GUARDRAIL,
    build_user_visible_workflow_prompt,
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


def test_user_visible_prompt_omits_execution_guardrail() -> None:
    full_prompt = (
        "Find the accounts with open renewals."
        f"\n\n{WORKFLOW_NESTING_GUARDRAIL}"
        "\n\nInput parameters:\n- owner (string, required): \"sam\""
    )

    visible_prompt = build_user_visible_workflow_prompt(full_prompt)

    assert WORKFLOW_NESTING_GUARDRAIL not in visible_prompt
    assert "Find the accounts with open renewals." in visible_prompt
    assert "Input parameters:" in visible_prompt

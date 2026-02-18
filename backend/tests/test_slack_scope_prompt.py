from agents.orchestrator import _format_slack_scope_context


def test_slack_scope_prompt_mentions_ambiguity_and_thread_filter() -> None:
    prompt = _format_slack_scope_context("C123", "1729364.001")

    assert "ask a brief clarification question" in prompt
    assert "custom_fields->>'channel_id' = 'C123'" in prompt
    assert "custom_fields->>'thread_ts' = '1729364.001'" in prompt
    assert "this chat" in prompt


def test_slack_scope_prompt_no_context_without_channel() -> None:
    prompt = _format_slack_scope_context(None, "1729364.001")

    assert prompt == ""

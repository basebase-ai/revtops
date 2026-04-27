from agents.orchestrator import _format_slack_scope_context


def test_slack_scope_prompt_mentions_ambiguity_and_thread_filter() -> None:
    prompt = _format_slack_scope_context("C123", "1729364.001")

    assert "ask a brief clarification question" in prompt
    assert "check that quoted history first" in prompt
    assert "custom_fields->>'channel_id' = 'C123'" in prompt
    assert "custom_fields->>'thread_ts' = '1729364.001'" in prompt
    assert "this chat" in prompt


def test_slack_scope_prompt_no_context_without_channel() -> None:
    prompt = _format_slack_scope_context(None, "1729364.001")

    assert prompt == ""


def test_slack_scope_prompt_private_history_note_only_for_private_channels() -> None:
    mention_private_prompt = _format_slack_scope_context(
        "C123",
        "1729364.001",
        slack_channel_type="private_channel",
        source="slack_mention",
    )
    dm_prompt = _format_slack_scope_context(
        "D123",
        "1729364.001",
        slack_channel_type="im",
        source="slack_dm",
    )
    group_dm_prompt = _format_slack_scope_context(
        "GDM123",
        "1729364.001",
        slack_channel_type="mpim",
        source="slack_dm",
    )

    expected_note = "We do not proactively store private channel history outside of direct conversations with the bot."
    assert expected_note in mention_private_prompt
    assert expected_note not in dm_prompt
    assert expected_note not in group_dm_prompt

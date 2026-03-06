from typing import Any

from agents.orchestrator import _trim_context


def test_trim_context_near_limit_strips_tool_payloads_without_dropping_messages() -> None:
    """Near-limit retry keeps history length stable while shrinking bulky tool blocks."""
    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me look that up."},
                {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "run_sql_query",
                    "input": {
                        "query": "SELECT * FROM opportunities WHERE amount > 10000",
                        "limit": 5000,
                    },
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": "x" * 10000,
                }
            ],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": "Summarize the top opportunities."}],
        },
    ]

    trimmed = _trim_context(messages, trimmable_history=2, retry_number=0)

    assert len(messages) == 3
    assert trimmed["trimmable_history"] == 2
    assert "stripped tool content" in trimmed["description"]

    assistant_blocks = messages[0]["content"]
    assert isinstance(assistant_blocks, list)
    assert assistant_blocks[1] == {
        "type": "tool_use",
        "id": "tool-1",
        "name": "run_sql_query",
        "input": {},
    }

    tool_result_blocks = messages[1]["content"]
    assert isinstance(tool_result_blocks, list)
    assert tool_result_blocks[0] == {
        "type": "tool_result",
        "tool_use_id": "tool-1",
        "content": "[result trimmed to save context space]",
    }

    current_user_blocks = messages[2]["content"]
    assert isinstance(current_user_blocks, list)
    assert current_user_blocks[0]["text"] == "Summarize the top opportunities."


def test_trim_context_second_retry_drops_oldest_history_but_keeps_current_prompt() -> None:
    """Follow-up retry should drop old history while preserving the latest user prompt."""
    messages: list[dict[str, Any]] = [
        {"role": "assistant", "content": [{"type": "text", "text": "h1"}]},
        {"role": "user", "content": [{"type": "text", "text": "h2"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "h3"}]},
        {"role": "user", "content": [{"type": "text", "text": "latest user prompt"}]},
    ]

    trimmed = _trim_context(messages, trimmable_history=3, retry_number=1)

    # retry_number > 0 drops half (floor) of trimmable history, at least one message
    assert trimmed["trimmable_history"] == 2
    assert "dropped" in trimmed["description"]

    assert len(messages) == 3
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"][0]["text"] == "latest user prompt"

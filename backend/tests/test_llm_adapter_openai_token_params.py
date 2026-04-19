from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.llm_adapter import OpenAIAdapter


class _EmptyAsyncIterator:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def test_openai_gpt5_uses_max_completion_tokens():
    adapter = OpenAIAdapter(api_key="test-key")

    assert adapter._build_token_limit_kwargs(model="gpt-5", max_tokens=1234) == {
        "max_completion_tokens": 1234
    }


def test_openai_legacy_models_use_max_tokens():
    adapter = OpenAIAdapter(api_key="test-key")

    assert adapter._build_token_limit_kwargs(model="gpt-4o-mini", max_tokens=4321) == {
        "max_tokens": 4321
    }


def test_openai_gpt5_with_provider_prefix_uses_max_completion_tokens():
    adapter = OpenAIAdapter(api_key="test-key")

    assert adapter._build_token_limit_kwargs(
        model="openai/GPT-5-mini",
        max_tokens=777,
    ) == {"max_completion_tokens": 777}


def test_openai_format_messages_coerces_null_content_to_empty_string():
    adapter = OpenAIAdapter(api_key="test-key")

    formatted = adapter.format_messages_for_api(
        [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "fn", "input": {}}]},
            {"role": "user", "content": None},
        ]
    )

    assert formatted[0]["content"] == ""
    assert formatted[1]["content"] == ""


def test_openai_format_messages_coerces_tool_result_null_content_to_string():
    adapter = OpenAIAdapter(api_key="test-key")

    formatted = adapter.format_messages_for_api(
        [
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": None}],
            }
        ]
    )

    assert formatted == [{"role": "tool", "tool_call_id": "tool-1", "content": ""}]


def test_openai_format_messages_is_idempotent_for_openai_tool_sequence():
    adapter = OpenAIAdapter(api_key="test-key")

    formatted = adapter.format_messages_for_api(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {"name": "run_sql_query", "arguments": "{\"query\":\"select 1\"}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_123", "content": "{\"row_count\":1}"},
        ]
    )

    assert formatted == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "run_sql_query", "arguments": "{\"query\":\"select 1\"}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_123", "content": "{\"row_count\":1}"},
    ]


@pytest.mark.asyncio
async def test_openai_stream_does_not_pass_duplicate_model_kwarg():
    adapter = OpenAIAdapter(api_key="test-key")
    create_mock = AsyncMock(return_value=_EmptyAsyncIterator())
    adapter._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    events = [
        event
        async for event in adapter.stream(
            model="gpt-4o-mini",
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=42,
        )
    ]

    assert events == []
    assert create_mock.await_count == 1
    call_kwargs = create_mock.await_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"

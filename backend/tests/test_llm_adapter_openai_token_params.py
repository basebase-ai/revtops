from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIStatusError

from services.llm_adapter import OpenAIAdapter


class _EmptyAsyncIterator:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def _openai_api_status_error(message: str, status_code: int = 404) -> APIStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status_code=status_code, request=request, headers={"x-request-id": "req_test"})
    return APIStatusError(
        message=message,
        response=response,
        body={"error": {"type": "not_found_error", "message": message}},
    )


def test_openai_gpt5_uses_max_completion_tokens():
    adapter = OpenAIAdapter(api_key="test-key")

    assert adapter._build_token_limit_kwargs(model="gpt-5.5", max_tokens=1234) == {
        "max_completion_tokens": 1234
    }


def test_openai_legacy_models_use_max_tokens():
    adapter = OpenAIAdapter(api_key="test-key")

    assert adapter._build_token_limit_kwargs(model="gpt-4o-mini", max_tokens=4321) == {
        "max_tokens": 4321
    }




def test_openai_legacy_gpt5_uses_max_completion_tokens():
    adapter = OpenAIAdapter(api_key="test-key")

    assert adapter._build_token_limit_kwargs(model="gpt-5", max_tokens=333) == {
        "max_completion_tokens": 333
    }


def test_openai_non_hyphenated_gpt55_uses_max_completion_tokens():
    adapter = OpenAIAdapter(api_key="test-key")

    assert adapter._build_token_limit_kwargs(model="gpt5.5", max_tokens=222) == {
        "max_completion_tokens": 222
    }
def test_openai_gpt5_with_provider_prefix_uses_max_completion_tokens():
    adapter = OpenAIAdapter(api_key="test-key")

    assert adapter._build_token_limit_kwargs(
        model="openai/GPT-5.5-mini",
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


@pytest.mark.asyncio
async def test_openai_stream_falls_back_when_gpt5_not_found():
    adapter = OpenAIAdapter(api_key="test-key")
    create_mock = AsyncMock(
        side_effect=[
            _openai_api_status_error("model: gpt-5.5"),
            _EmptyAsyncIterator(),
        ]
    )
    adapter._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    events = [
        event
        async for event in adapter.stream(
            model="gpt-5.5",
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=42,
        )
    ]

    assert events == []
    assert create_mock.await_count == 2
    assert create_mock.await_args_list[0].kwargs["model"] == "gpt-5.5"
    assert create_mock.await_args_list[1].kwargs["model"] == "gpt-5"


@pytest.mark.asyncio
async def test_openai_complete_falls_back_when_gpt5_not_found():
    adapter = OpenAIAdapter(api_key="test-key")
    completion_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="fallback answer", tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
    )
    create_mock = AsyncMock(
        side_effect=[
            _openai_api_status_error("model: gpt-5.5"),
            completion_response,
        ]
    )
    adapter._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    completed = await adapter.complete(
        model="gpt-5.5",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=42,
    )

    assert completed.input_tokens == 1
    assert completed.output_tokens == 2
    assert create_mock.await_count == 2
    assert create_mock.await_args_list[0].kwargs["model"] == "gpt-5.5"
    assert create_mock.await_args_list[1].kwargs["model"] == "gpt-5"

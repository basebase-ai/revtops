from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.llm_adapter import OpenAIAdapter


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


@pytest.mark.asyncio
async def test_openai_token_kwarg_falls_back_when_preferred_is_rejected():
    adapter = OpenAIAdapter(api_key="test-key")
    create_mock = AsyncMock(
        side_effect=[
            TypeError(
                "AsyncCompletions.create() got an unexpected keyword argument "
                "'max_completion_tokens'"
            ),
            SimpleNamespace(id="ok"),
        ]
    )
    adapter._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    result = await adapter._create_chat_completion_with_token_fallback(
        model="gpt-5",
        max_tokens=100,
        messages=[{"role": "system", "content": "hi"}],
    )

    assert result.id == "ok"
    assert create_mock.await_count == 2
    first_call_kwargs = create_mock.await_args_list[0].kwargs
    second_call_kwargs = create_mock.await_args_list[1].kwargs
    assert "max_completion_tokens" in first_call_kwargs
    assert "max_tokens" not in first_call_kwargs
    assert "max_tokens" in second_call_kwargs
    assert "max_completion_tokens" not in second_call_kwargs


@pytest.mark.asyncio
async def test_openai_token_kwarg_falls_back_for_legacy_model_when_needed():
    adapter = OpenAIAdapter(api_key="test-key")
    create_mock = AsyncMock(
        side_effect=[
            TypeError(
                "AsyncCompletions.create() got an unexpected keyword argument "
                "'max_tokens'"
            ),
            SimpleNamespace(id="ok"),
        ]
    )
    adapter._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    result = await adapter._create_chat_completion_with_token_fallback(
        model="gpt-4o-mini",
        max_tokens=100,
        messages=[{"role": "system", "content": "hi"}],
    )

    assert result.id == "ok"
    assert create_mock.await_count == 2
    first_call_kwargs = create_mock.await_args_list[0].kwargs
    second_call_kwargs = create_mock.await_args_list[1].kwargs
    assert "max_tokens" in first_call_kwargs
    assert "max_completion_tokens" not in first_call_kwargs
    assert "max_completion_tokens" in second_call_kwargs
    assert "max_tokens" not in second_call_kwargs

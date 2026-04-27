from services.llm_adapter import LLMConfig, OpenAIAdapter, get_adapter


def test_get_adapter_supports_alibaba_provider_alias() -> None:
    config = LLMConfig(
        provider="alibaba",  # type: ignore[arg-type]
        primary_model="qwen3.6-plus",
        cheap_model="qwen3-30b-a3b-instruct-2507",
        workflow_model="qwen3.6-plus",
        api_key="test-key",
    )

    adapter = get_adapter(config)

    assert isinstance(adapter, OpenAIAdapter)

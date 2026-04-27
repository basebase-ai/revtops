import asyncio

from services.llm_provider import (
    _infer_provider_from_model_name,
    is_model_allowed,
    provider_for_model,
    resolve_api_key_for_provider,
    resolve_llm_config,
)


def test_infer_provider_from_model_name() -> None:
    assert _infer_provider_from_model_name("claude-haiku-4-5-20251001") == "anthropic"
    assert _infer_provider_from_model_name("gpt-5.5-mini") == "openai"
    assert _infer_provider_from_model_name("gemini-2.5-flash") == "gemini"
    assert _infer_provider_from_model_name("MiniMax-M2.7-highspeed") == "minimax"
    assert _infer_provider_from_model_name("qwen3-max") == "qwen"
    assert _infer_provider_from_model_name("qwq-plus") == "qwen"
    assert _infer_provider_from_model_name("some-unknown-model") is None


def test_resolve_llm_config_uses_provider_defaults_for_mismatched_global_models(monkeypatch) -> None:
    from services import llm_provider

    monkeypatch.setattr(llm_provider, "_DEFAULT_PROVIDER", "openai")
    monkeypatch.setitem(llm_provider._GLOBAL_PROVIDER_KEYS, "openai", "test-openai-key")
    monkeypatch.setattr(llm_provider.settings, "DEFAULT_PRIMARY_MODEL", "claude-opus-4-6")
    monkeypatch.setattr(llm_provider.settings, "DEFAULT_CHEAP_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setattr(llm_provider.settings, "ALL_MODEL_STRINGS", "")

    config = asyncio.run(resolve_llm_config(None))

    assert config.provider == "openai"
    assert config.primary_model == "gpt-5.5"
    assert config.cheap_model == "gpt-5.5-mini"


def test_resolve_llm_config_logs_when_model_fallback_engaged(monkeypatch, caplog) -> None:
    from services import llm_provider

    monkeypatch.setattr(llm_provider, "_DEFAULT_PROVIDER", "openai")
    monkeypatch.setitem(llm_provider._GLOBAL_PROVIDER_KEYS, "openai", "test-openai-key")
    monkeypatch.setattr(llm_provider.settings, "DEFAULT_PRIMARY_MODEL", "claude-opus-4-6")
    monkeypatch.setattr(llm_provider.settings, "DEFAULT_CHEAP_MODEL", "")
    monkeypatch.setattr(llm_provider.settings, "ALL_MODEL_STRINGS", "")

    caplog.set_level("INFO")
    _ = asyncio.run(resolve_llm_config(None))

    assert any(
        "Model fallback engaged (quick/same-family)" in record.message for record in caplog.records
    )


def test_resolve_api_key_for_provider_uses_global_key(monkeypatch) -> None:
    from services import llm_provider

    monkeypatch.setitem(llm_provider._GLOBAL_PROVIDER_KEYS, "gemini", "test-gemini-key")

    key = asyncio.run(resolve_api_key_for_provider("gemini", None))
    assert key == "test-gemini-key"


def test_model_allowlist_accepts_gpt55_aliases(monkeypatch) -> None:
    from services import llm_provider

    monkeypatch.setattr(llm_provider.settings, "ALL_MODEL_STRINGS", "gpt5.5:openai,gpt5.5-mini:openai")

    assert is_model_allowed("gpt-5.5")
    assert is_model_allowed("gpt-5.5-mini")
    assert provider_for_model("gpt-5.5") == "openai"


def test_resolve_llm_config_infers_provider_from_model_prefix_when_allowlist_omits_provider(
    monkeypatch,
) -> None:
    from services import llm_provider

    monkeypatch.setattr(llm_provider, "_DEFAULT_PROVIDER", "anthropic")
    monkeypatch.setitem(llm_provider._GLOBAL_PROVIDER_KEYS, "openai", "test-openai-key")
    monkeypatch.setattr(llm_provider.settings, "DEFAULT_PRIMARY_MODEL", "")
    monkeypatch.setattr(llm_provider.settings, "DEFAULT_CHEAP_MODEL", "")
    monkeypatch.setattr(llm_provider.settings, "ALL_MODEL_STRINGS", "gpt-5.5")

    class _Org:
        handle = "acme"
        llm_provider = None
        llm_primary_model = "gpt-5.5"
        llm_cheap_model = "gpt-5.5-mini"
        llm_workflow_model = None

    async def _fake_load_org(_organization_id):
        return _Org()

    monkeypatch.setattr(llm_provider, "_load_organization_for_llm", _fake_load_org)

    config = asyncio.run(resolve_llm_config("00000000-0000-0000-0000-000000000001"))

    assert config.provider == "openai"
    assert config.primary_model == "gpt-5.5"
    assert config.cheap_model == "gpt-5.5-mini"

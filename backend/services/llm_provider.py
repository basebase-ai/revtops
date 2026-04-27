"""
Resolve per-org LLM configuration from database + environment variables.

Resolution order for API keys:
1. LLM_KEY__<org_handle> environment variable (org-specific)
2. Global provider key (ANTHROPIC_API_KEY, MINIMAX_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, QWEN_API_KEY)

Provider/model resolution:
1. Organization.llm_provider / llm_primary_model / llm_cheap_model / llm_workflow_model (DB)
2. Global defaults from config.settings
"""

from __future__ import annotations

import logging
import os
from uuid import UUID

from config import settings
from services.llm_adapter import (
    LLMConfig,
    LLMProvider,
    PROVIDER_DEFAULT_MODELS,
    AnthropicAdapter,
    OpenAIAdapter,
    get_adapter,
)

logger = logging.getLogger(__name__)

_GLOBAL_PROVIDER_KEYS: dict[str, str | None] = {
    "anthropic": settings.ANTHROPIC_API_KEY,
    "minimax": getattr(settings, "MINIMAX_API_KEY", None),
    "openai": settings.OPENAI_API_KEY,
    "gemini": getattr(settings, "GEMINI_API_KEY", None),
    "qwen": getattr(settings, "QWEN_API_KEY", None),
}

_DEFAULT_PROVIDER: LLMProvider = "anthropic"


async def resolve_llm_config(
    organization_id: str | UUID | None,
) -> LLMConfig:
    """Resolve the LLM provider, model, and API key for an organization.

    Falls back to global defaults from environment when the org has no overrides.
    """
    provider: LLMProvider = _DEFAULT_PROVIDER
    primary_model: str | None = None
    cheap_model: str | None = None
    workflow_model: str | None = None
    org_handle: str | None = None

    if organization_id is not None:
        try:
            org = await _load_organization_for_llm(organization_id)
            if org is not None:
                org_handle = org.handle
                if org.llm_provider:
                    provider = org.llm_provider  # type: ignore[assignment]
                if org.llm_primary_model:
                    primary_model = org.llm_primary_model
                if org.llm_cheap_model:
                    cheap_model = org.llm_cheap_model
                if org.llm_workflow_model:
                    workflow_model = org.llm_workflow_model
        except Exception:
            logger.warning(
                "Failed to load org LLM config for %s; using global defaults",
                organization_id,
                exc_info=True,
            )

    # Infer provider from model when not explicitly set
    if provider == _DEFAULT_PROVIDER:
        inferred_model: str | None = primary_model or cheap_model
        if inferred_model:
            inferred: str | None = provider_for_model(inferred_model)
            if not inferred:
                inferred = _infer_provider_from_model_name(inferred_model)
            if inferred:
                provider = inferred  # type: ignore[assignment]

    # Resolve models — org override → global env defaults → per-provider hardcoded defaults
    provider_defaults: dict[str, str] = PROVIDER_DEFAULT_MODELS.get(
        provider, PROVIDER_DEFAULT_MODELS["anthropic"]
    )
    if not primary_model:
        primary_model = _select_compatible_model(
            requested_model=settings.DEFAULT_PRIMARY_MODEL,
            provider=provider,
            fallback_model=provider_defaults["primary"],
            model_role="primary",
        )
    if not cheap_model:
        cheap_model = _select_compatible_model(
            requested_model=settings.DEFAULT_CHEAP_MODEL,
            provider=provider,
            fallback_model=provider_defaults["cheap"],
            model_role="cheap",
        )
    if not workflow_model:
        workflow_model = primary_model

    # Resolve API key: org-specific env var → global provider key
    api_key: str = _resolve_api_key(provider, org_handle)

    return LLMConfig(
        provider=provider,
        primary_model=primary_model,
        cheap_model=cheap_model,
        workflow_model=workflow_model,
        api_key=api_key,
    )


async def resolve_api_key_for_provider(
    provider: LLMProvider,
    organization_id: str | UUID | None,
) -> str:
    """Resolve API key for a specific provider using org-scoped key override if available."""
    org_handle: str | None = None
    if organization_id is not None:
        try:
            org = await _load_organization_for_llm(organization_id)
            if org is not None:
                org_handle = org.handle
        except Exception:
            logger.warning(
                "Failed to load org handle for provider-key lookup organization_id=%s provider=%s",
                organization_id,
                provider,
                exc_info=True,
            )
    return _resolve_api_key(provider, org_handle)


async def _load_organization_for_llm(organization_id: str | UUID) -> object | None:
    """Load organization row used by LLM configuration helpers."""
    from models.database import get_admin_session
    from models.organization import Organization

    org_uuid: UUID = (
        organization_id if isinstance(organization_id, UUID) else UUID(str(organization_id))
    )
    async with get_admin_session() as session:
        return await session.get(Organization, org_uuid)


def _resolve_api_key(provider: LLMProvider, org_handle: str | None) -> str:
    """Resolve the API key for a provider, checking org-specific env vars first."""
    if org_handle:
        env_key: str | None = os.environ.get(f"LLM_KEY__{org_handle}")
        if env_key:
            return env_key

    global_key: str | None = _GLOBAL_PROVIDER_KEYS.get(provider)
    if global_key:
        return global_key

    logger.error("No API key found for provider %s (org_handle=%s)", provider, org_handle)
    return ""


def _select_compatible_model(
    *,
    requested_model: str | None,
    provider: LLMProvider,
    fallback_model: str,
    model_role: str,
) -> str:
    """Return a model compatible with provider, falling back when needed."""
    if not requested_model:
        logger.info(
            "[LLMProvider] Model fallback engaged (quick/same-family): role=%s provider=%s requested_model=<unset> selected_model=%s reason=missing_requested_model",
            model_role,
            provider,
            fallback_model,
        )
        return fallback_model

    mapped_provider: str | None = provider_for_model(requested_model)
    if mapped_provider and mapped_provider != provider:
        logger.warning(
            "Ignoring %s model '%s' for provider '%s' (belongs to '%s'); using '%s' instead",
            model_role,
            requested_model,
            provider,
            mapped_provider,
            fallback_model,
        )
        logger.info(
            "[LLMProvider] Model fallback engaged (quick/same-family): role=%s provider=%s requested_model=%s selected_model=%s reason=provider_mismatch(mapped=%s)",
            model_role,
            provider,
            requested_model,
            fallback_model,
            mapped_provider,
        )
        return fallback_model

    inferred_provider: str | None = _infer_provider_from_model_name(requested_model)
    if inferred_provider and inferred_provider != provider:
        logger.warning(
            "Ignoring %s model '%s' for provider '%s' (inferred '%s'); using '%s' instead",
            model_role,
            requested_model,
            provider,
            inferred_provider,
            fallback_model,
        )
        logger.info(
            "[LLMProvider] Model fallback engaged (quick/same-family): role=%s provider=%s requested_model=%s selected_model=%s reason=provider_mismatch(inferred=%s)",
            model_role,
            provider,
            requested_model,
            fallback_model,
            inferred_provider,
        )
        return fallback_model

    return requested_model


def _infer_provider_from_model_name(model: str) -> str | None:
    """Infer provider for common model naming conventions."""
    normalized: str = model.strip().lower()
    if normalized.startswith("claude"):
        return "anthropic"
    if normalized.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    if normalized.startswith("gemini"):
        return "gemini"
    if normalized.startswith(("qwen", "qwq")):
        return "qwen"
    if normalized.startswith("minimax"):
        return "minimax"
    return None


async def get_org_adapter(
    organization_id: str | UUID | None,
) -> AnthropicAdapter | OpenAIAdapter:
    """Convenience: resolve config and return the appropriate adapter."""
    config: LLMConfig = await resolve_llm_config(organization_id)
    return get_adapter(config)


async def get_org_llm_config_and_adapter(
    organization_id: str | UUID | None,
) -> tuple[LLMConfig, AnthropicAdapter | OpenAIAdapter]:
    """Resolve config and adapter in a single call (avoids duplicate DB lookups)."""
    config: LLMConfig = await resolve_llm_config(organization_id)
    adapter: AnthropicAdapter | OpenAIAdapter = get_adapter(config)
    return config, adapter


def _parse_model_map() -> dict[str, str]:
    """Parse ALL_MODEL_STRINGS ('model:provider,...') into {model: provider}."""
    raw: str = settings.ALL_MODEL_STRINGS.strip()
    if not raw:
        return {}
    result: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            model, provider = entry.rsplit(":", 1)
            result[model.strip()] = provider.strip()
        else:
            result[entry] = ""
    return result


def _model_aliases(model: str) -> tuple[str, ...]:
    """Return acceptable aliases for known model naming variations."""
    normalized: str = model.strip()
    aliases: list[str] = [normalized]
    # Accept both gpt-5.5 and gpt5.5 naming variants (including mini/nano).
    if normalized.startswith("gpt-5.5"):
        aliases.append(normalized.replace("gpt-5.5", "gpt5.5", 1))
    elif normalized.startswith("gpt5.5"):
        aliases.append(normalized.replace("gpt5.5", "gpt-5.5", 1))
    return tuple(dict.fromkeys(aliases))


def get_model_provider_map() -> dict[str, str]:
    """Return the full {model_name: provider} map from ALL_MODEL_STRINGS."""
    return _parse_model_map()


def get_allowed_models() -> list[str]:
    """Return the allowlist of model names from ALL_MODEL_STRINGS.

    Returns an empty list when unset (meaning no restriction).
    """
    return list(_parse_model_map().keys())


def provider_for_model(model: str) -> str | None:
    """Look up the provider for a model name. Returns None if unknown."""
    model_map: dict[str, str] = _parse_model_map()
    for alias in _model_aliases(model):
        provider: str | None = model_map.get(alias)
        if provider is not None:
            return provider or None
    return None


def is_model_allowed(model: str) -> bool:
    """Check whether a model name is in the configured allowlist.

    Always returns True when ALL_MODEL_STRINGS is empty (no restriction).
    """
    model_map: dict[str, str] = _parse_model_map()
    if not model_map:
        return True
    return any(alias in model_map for alias in _model_aliases(model))

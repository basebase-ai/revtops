"""
Provider-agnostic LLM adapter layer.

Two adapter implementations cover five providers:
- AnthropicAdapter: Anthropic (native) + MiniMax (base_url override)
- OpenAIAdapter: OpenAI (native) + Gemini/Qwen (base_url override)

Both yield a common StreamEvent protocol so the orchestrator and services
are decoupled from vendor-specific SDK details.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol

from anthropic import APIStatusError as AnthropicAPIStatusError, AsyncAnthropic
from openai import APIStatusError as OpenAIAPIStatusError, AsyncOpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Common types
# ---------------------------------------------------------------------------

LLMProvider = Literal["anthropic", "minimax", "openai", "gemini", "qwen"]

PROVIDER_BASE_URLS: dict[str, str] = {
    "minimax": "https://api.minimax.io/anthropic",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
}

PROVIDER_DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "anthropic": {"primary": "claude-opus-4-6", "cheap": "claude-haiku-4-5-20251001"},
    "minimax": {"primary": "MiniMax-M2.7", "cheap": "MiniMax-M2.7-highspeed"},
    "openai": {"primary": "gpt-5.5", "cheap": "gpt-5.5-mini"},
    "gemini": {"primary": "gemini-2.5-pro", "cheap": "gemini-2.5-flash"},
    "qwen": {"primary": "qwen3-max", "cheap": "qwen-flash"},
}


@dataclass(frozen=True)
class LLMConfig:
    """Resolved LLM configuration for a request."""

    provider: LLMProvider
    primary_model: str
    cheap_model: str
    workflow_model: str
    api_key: str
    base_url: str | None = None


@dataclass
class StreamEvent:
    """Common stream event emitted by all adapters."""

    type: Literal[
        "thinking_start",
        "thinking_delta",
        "thinking_stop",
        "text_start",
        "text_delta",
        "text_stop",
        "tool_use_start",
        "tool_input_delta",
        "tool_use_stop",
        "usage",
    ]
    text: str | None = None
    tool_id: str | None = None
    tool_name: str | None = None
    tool_input_json: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class ContentBlock:
    """Provider-agnostic content block (for completed messages)."""

    type: Literal["thinking", "text", "tool_use"]
    text: str | None = None
    thinking: str | None = None
    signature: str | None = None
    tool_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None


@dataclass
class CompletedMessage:
    """Result of a non-streaming LLM call."""

    content_blocks: list[ContentBlock]
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ToolDef:
    """Provider-agnostic tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any]


def _downgrade_document_blocks_for_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace user ``document`` blocks with provider-safe text blocks."""
    return [_downgrade_document_blocks_in_message(msg) for msg in messages]


def _downgrade_document_blocks_in_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Replace ``document`` content blocks with extracted-text blocks.

    Only user messages can contain multimodal content blocks, so
    assistant/tool messages are returned unchanged.
    """
    content: Any = msg.get("content")
    if msg.get("role") != "user" or not isinstance(content, list):
        return msg

    needs_rewrite: bool = any(
        isinstance(b, dict) and b.get("type") == "document" for b in content
    )
    if not needs_rewrite:
        return msg

    from services.file_handler import StoredFile, pdf_to_text_block
    import base64 as _b64

    new_blocks: list[dict[str, Any]] = []
    pdf_counter: int = 0
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "document":
            new_blocks.append(block)
            continue

        source: dict[str, Any] = block.get("source", {})
        if source.get("media_type") != "application/pdf":
            new_blocks.append(block)
            continue

        pdf_counter += 1
        filename: str = block.get("title") or f"attachment-{pdf_counter}.pdf"

        try:
            raw_data: bytes = _b64.standard_b64decode(source.get("data", ""))
        except Exception as exc:
            logger.warning("Failed to decode PDF base64 for %s: %s", filename, exc)
            new_blocks.append({
                "type": "text",
                "text": f"[Attached PDF '{filename}' could not be decoded]",
            })
            continue

        sf = StoredFile(
            upload_id="",
            filename=filename,
            mime_type="application/pdf",
            size=len(raw_data),
            data=raw_data,
        )
        new_blocks.append(pdf_to_text_block(sf))

    logger.info(
        "[LLMAdapter] Downgraded document blocks for user message (original_blocks=%d downgraded_blocks=%d)",
        len(content),
        len(new_blocks),
    )
    return {**msg, "content": new_blocks}


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class LLMAdapter(Protocol):
    """Protocol that all provider adapters implement."""

    async def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolDef] | None = None,
        thinking: bool = False,
        max_tokens: int = 32768,
    ) -> AsyncIterator[StreamEvent]: ...

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
    ) -> CompletedMessage: ...

    def format_tools(self, tools: list[ToolDef]) -> list[dict[str, Any]]: ...

    def format_messages_for_api(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]: ...

    def build_completed_content(
        self, raw_response: Any
    ) -> list[ContentBlock]: ...


# ---------------------------------------------------------------------------
# Anthropic adapter (covers Anthropic + MiniMax)
# ---------------------------------------------------------------------------


class AnthropicAdapter:
    """Adapter for Anthropic Messages API (also used by MiniMax via base_url)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        supports_document_blocks: bool = True,
    ) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url is not None:
            kwargs["base_url"] = base_url
        self._client: AsyncAnthropic = AsyncAnthropic(**kwargs)
        self._supports_document_blocks: bool = supports_document_blocks

    # -- streaming ----------------------------------------------------------

    async def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolDef] | None = None,
        thinking: bool = False,
        max_tokens: int = 32768,
    ) -> AsyncIterator[StreamEvent]:
        api_messages: list[dict[str, Any]] = self.format_messages_for_api(messages)
        api_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": api_messages,
        }
        if tools:
            api_kwargs["tools"] = self.format_tools(tools)
        if thinking:
            api_kwargs["thinking"] = {"type": "adaptive"}

        self._current_block_type = "text"
        async with self._client.messages.stream(**api_kwargs) as stream:
            async for event in stream:
                for se in self._translate_event(event):
                    yield se

            final = await stream.get_final_message()
            if final and hasattr(final, "usage"):
                yield StreamEvent(
                    type="usage",
                    input_tokens=final.usage.input_tokens,
                    output_tokens=final.usage.output_tokens,
                )

    _current_block_type: str = "text"

    def _translate_event(self, event: Any) -> list[StreamEvent]:
        """Translate a single Anthropic stream event into common StreamEvents."""
        results: list[StreamEvent] = []

        if event.type == "content_block_start":
            block = event.content_block
            self._current_block_type = block.type
            if block.type == "thinking":
                results.append(StreamEvent(type="thinking_start"))
            elif block.type == "text":
                results.append(StreamEvent(type="text_start"))
            elif block.type == "tool_use":
                results.append(
                    StreamEvent(
                        type="tool_use_start",
                        tool_id=block.id,
                        tool_name=block.name,
                    )
                )

        elif event.type == "content_block_delta":
            delta = event.delta
            if delta.type == "thinking_delta":
                results.append(StreamEvent(type="thinking_delta", text=delta.thinking))
            elif delta.type == "signature_delta":
                pass
            elif delta.type == "text_delta":
                results.append(StreamEvent(type="text_delta", text=delta.text))
            elif delta.type == "input_json_delta":
                results.append(
                    StreamEvent(type="tool_input_delta", tool_input_json=delta.partial_json)
                )

        elif event.type == "content_block_stop":
            if self._current_block_type == "tool_use":
                results.append(StreamEvent(type="tool_use_stop"))
            elif self._current_block_type == "thinking":
                results.append(StreamEvent(type="thinking_stop"))
            else:
                results.append(StreamEvent(type="text_stop"))

        return results

    # -- non-streaming (simple calls) --------------------------------------

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
    ) -> CompletedMessage:
        api_messages: list[dict[str, Any]] = self.format_messages_for_api(messages)
        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=api_messages,
        )
        blocks: list[ContentBlock] = self.build_completed_content(response)
        return CompletedMessage(
            content_blocks=blocks,
            input_tokens=response.usage.input_tokens if response.usage else 0,
            output_tokens=response.usage.output_tokens if response.usage else 0,
        )

    # -- formatting helpers -------------------------------------------------

    def format_tools(self, tools: list[ToolDef]) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]

    def format_messages_for_api(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if self._supports_document_blocks:
            return messages
        return _downgrade_document_blocks_for_messages(messages)

    def build_completed_content(self, raw_response: Any) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        for block in raw_response.content:
            if block.type == "thinking":
                blocks.append(
                    ContentBlock(
                        type="thinking",
                        thinking=block.thinking,
                        signature=block.signature,
                    )
                )
            elif block.type == "text":
                blocks.append(ContentBlock(type="text", text=block.text))
            elif block.type == "tool_use":
                blocks.append(
                    ContentBlock(
                        type="tool_use",
                        tool_id=block.id,
                        tool_name=block.name,
                        tool_input=block.input,
                    )
                )
        return blocks


# ---------------------------------------------------------------------------
# OpenAI adapter (covers OpenAI + Gemini)
# ---------------------------------------------------------------------------


class OpenAIAdapter:
    """Adapter for OpenAI Chat Completions API (also used by Gemini via base_url)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        supports_document_blocks: bool = False,
    ) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url is not None:
            kwargs["base_url"] = base_url
        self._client: AsyncOpenAI = AsyncOpenAI(**kwargs)
        self._supports_document_blocks: bool = supports_document_blocks

    def _build_token_limit_kwargs(self, *, model: str, max_tokens: int) -> dict[str, int]:
        """Map token limit parameter name based on OpenAI model requirements."""
        # Newer reasoning families (e.g. gpt-5 / gpt-5.5 / o-series) reject `max_tokens`.
        normalized_model: str = model.strip().lower().split("/")[-1]
        uses_completion_tokens: bool = normalized_model.startswith(("gpt-5", "gpt5", "o"))
        token_param_name: str = (
            "max_completion_tokens" if uses_completion_tokens else "max_tokens"
        )
        logger.debug(
            "OpenAI token limit param selected",
            extra={
                "model": model,
                "normalized_model": normalized_model,
                "token_param_name": token_param_name,
                "token_limit": max_tokens,
            },
        )
        return {token_param_name: max_tokens}

    def _openai_not_found_fallback_models(self, model: str) -> list[str]:
        """Return same-family model candidates when a model is not found."""
        normalized_model: str = model.strip().lower()
        prefix: str = ""
        base_model: str = normalized_model
        if "/" in normalized_model:
            prefix, base_model = normalized_model.split("/", 1)
            prefix = f"{prefix}/"

        if not base_model.startswith(("gpt-5", "gpt5")):
            return []

        canonical_base_model: str = base_model.replace("gpt5", "gpt-5", 1) if base_model.startswith("gpt5") else base_model

        variants: list[str] = []
        if canonical_base_model == "gpt-5.5":
            variants.extend(["gpt-5", "gpt-5.5-mini"])
        elif canonical_base_model == "gpt-5.5-mini":
            variants.append("gpt-5")
        elif canonical_base_model == "gpt-5.5-nano":
            variants.extend(["gpt-5.5-mini", "gpt-5"])

        fallback_models: list[str] = [f"{prefix}{variant}" for variant in variants if variant != base_model]
        if fallback_models:
            logger.warning(
                "[OpenAIAdapter] Model fallback engaged after 404 not_found: requested_model=%s fallback_candidates=%s",
                model,
                fallback_models,
            )
        return fallback_models

    # -- streaming ----------------------------------------------------------

    async def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolDef] | None = None,
        thinking: bool = False,
        max_tokens: int = 32768,
    ) -> AsyncIterator[StreamEvent]:
        api_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system}
        ] + self.format_messages_for_api(messages)

        api_kwargs: dict[str, Any] = {
            "messages": api_messages,
            "stream": True,
        }
        if tools:
            api_kwargs["tools"] = self.format_tools(tools)

        tool_calls_accum: dict[int, dict[str, str]] = {}
        current_text_started: bool = False

        stream: Any = None
        model_candidates: list[str] = [model, *self._openai_not_found_fallback_models(model)]
        for idx, candidate_model in enumerate(model_candidates):
            candidate_kwargs: dict[str, Any] = {
                **api_kwargs,
                "model": candidate_model,
                **self._build_token_limit_kwargs(model=candidate_model, max_tokens=max_tokens),
            }
            try:
                stream = await self._client.chat.completions.create(**candidate_kwargs)
                if idx > 0:
                    logger.info(
                        "[OpenAIAdapter] Stream call succeeded with fallback model=%s requested_model=%s",
                        candidate_model,
                        model,
                    )
                break
            except OpenAIAPIStatusError as exc:
                if exc.status_code == 404 and idx < len(model_candidates) - 1:
                    logger.warning(
                        "[OpenAIAdapter] Stream model unavailable (404), retrying fallback. requested_model=%s candidate_model=%s fallback_index=%d/%d",
                        model,
                        candidate_model,
                        idx + 1,
                        len(model_candidates),
                    )
                    continue
                raise

        if stream is None:
            raise RuntimeError("OpenAI stream initialization failed without a response stream")
        chunk: Any | None = None
        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if choice is None:
                continue

            delta = choice.delta
            if delta is None:
                continue

            # Text content
            if delta.content:
                if not current_text_started:
                    yield StreamEvent(type="text_start")
                    current_text_started = True
                yield StreamEvent(type="text_delta", text=delta.content)

            # Tool calls
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx: int = tc_delta.index if tc_delta.index is not None else 0
                    if idx not in tool_calls_accum:
                        tool_calls_accum[idx] = {
                            "id": tc_delta.id or f"call_{idx}",
                            "name": "",
                            "arguments": "",
                        }
                        if current_text_started:
                            yield StreamEvent(type="text_stop")
                            current_text_started = False

                    if tc_delta.id:
                        tool_calls_accum[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_accum[idx]["name"] = tc_delta.function.name
                            yield StreamEvent(
                                type="tool_use_start",
                                tool_id=tool_calls_accum[idx]["id"],
                                tool_name=tc_delta.function.name,
                            )
                        if tc_delta.function.arguments:
                            tool_calls_accum[idx]["arguments"] += tc_delta.function.arguments
                            yield StreamEvent(
                                type="tool_input_delta",
                                tool_input_json=tc_delta.function.arguments,
                            )

            # Finish
            if choice.finish_reason:
                if current_text_started:
                    yield StreamEvent(type="text_stop")
                for tc_data in tool_calls_accum.values():
                    yield StreamEvent(
                        type="tool_use_stop",
                        tool_id=tc_data["id"],
                        tool_name=tc_data["name"],
                    )

        # Usage (from last chunk if available)
        if chunk and hasattr(chunk, "usage") and chunk.usage:
            yield StreamEvent(
                type="usage",
                input_tokens=chunk.usage.prompt_tokens,
                output_tokens=chunk.usage.completion_tokens,
            )

    # -- non-streaming (simple calls) --------------------------------------

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
    ) -> CompletedMessage:
        api_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system}
        ] + self.format_messages_for_api(messages)

        response: Any = None
        model_candidates: list[str] = [model, *self._openai_not_found_fallback_models(model)]
        for idx, candidate_model in enumerate(model_candidates):
            try:
                response = await self._client.chat.completions.create(
                    model=candidate_model,
                    messages=api_messages,
                    **self._build_token_limit_kwargs(model=candidate_model, max_tokens=max_tokens),
                )
                if idx > 0:
                    logger.info(
                        "[OpenAIAdapter] Complete call succeeded with fallback model=%s requested_model=%s",
                        candidate_model,
                        model,
                    )
                break
            except OpenAIAPIStatusError as exc:
                if exc.status_code == 404 and idx < len(model_candidates) - 1:
                    logger.warning(
                        "[OpenAIAdapter] Complete model unavailable (404), retrying fallback. requested_model=%s candidate_model=%s fallback_index=%d/%d",
                        model,
                        candidate_model,
                        idx + 1,
                        len(model_candidates),
                    )
                    continue
                raise

        if response is None:
            raise RuntimeError("OpenAI complete call failed without a response")
        blocks: list[ContentBlock] = self.build_completed_content(response)
        usage = response.usage
        return CompletedMessage(
            content_blocks=blocks,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )

    # -- formatting helpers -------------------------------------------------

    def format_tools(self, tools: list[ToolDef]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    def format_messages_for_api(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Translate Anthropic-style stored messages to OpenAI chat format."""
        if not self._supports_document_blocks:
            messages = _downgrade_document_blocks_for_messages(messages)

        def _as_text(value: Any) -> str:
            """Coerce nullable/non-string content into API-safe text."""
            if value is None:
                return ""
            if isinstance(value, str):
                return value
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False)
            return str(value)

        result: list[dict[str, Any]] = []
        for msg in messages:
            role: str = msg.get("role", "user")
            content: Any = msg.get("content", "")

            # Pass through already-openai tool messages without dropping tool_call_id.
            if role == "tool":
                result.append({
                    "role": "tool",
                    "tool_call_id": _as_text(msg.get("tool_call_id", "")),
                    "content": _as_text(content),
                })
                continue

            # Idempotency: preserve already-openai assistant tool_calls.
            if role == "assistant" and isinstance(content, str) and msg.get("tool_calls"):
                normalized_tool_calls: list[dict[str, Any]] = []
                for tc in msg.get("tool_calls", []):
                    if not isinstance(tc, dict):
                        continue
                    function = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
                    raw_arguments = function.get("arguments", "")
                    arguments = (
                        raw_arguments
                        if isinstance(raw_arguments, str)
                        else json.dumps(raw_arguments, ensure_ascii=False)
                    )
                    normalized_tool_calls.append({
                        "id": _as_text(tc.get("id", "")),
                        "type": "function",
                        "function": {
                            "name": _as_text(function.get("name", "")),
                            "arguments": arguments,
                        },
                    })
                result.append({
                    "role": "assistant",
                    "content": _as_text(content),
                    "tool_calls": normalized_tool_calls,
                })
                continue

            if role == "user" and isinstance(content, list):
                # Check for tool_result blocks (Anthropic format → OpenAI tool messages)
                has_tool_results: bool = any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content
                )
                if has_tool_results:
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            result.append({
                                "role": "tool",
                                "tool_call_id": block.get("tool_use_id", ""),
                                "content": _as_text(block.get("content", "")),
                            })
                    continue

                # Image/text blocks — translate to OpenAI format
                openai_parts: list[dict[str, Any]] = []
                for block in content:
                    if isinstance(block, dict):
                        btype: str = block.get("type", "")
                        if btype == "text":
                            openai_parts.append(
                                {"type": "text", "text": _as_text(block.get("text", ""))}
                            )
                        elif btype == "image":
                            source = block.get("source", {})
                            media_type: str = source.get("media_type", "image/png")
                            data: str = source.get("data", "")
                            openai_parts.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:{media_type};base64,{data}"},
                            })
                    elif isinstance(block, str):
                        openai_parts.append({"type": "text", "text": block})
                result.append({"role": "user", "content": openai_parts})
                continue

            if role == "assistant" and isinstance(content, list):
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype == "text":
                        text_parts.append(_as_text(block.get("text", "")))
                    elif btype == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        })
                    # Skip thinking blocks — OpenAI doesn't use them

                msg_dict: dict[str, Any] = {
                    "role": "assistant",
                    "content": "\n".join(text_parts),
                }
                if tool_calls:
                    msg_dict["tool_calls"] = tool_calls
                result.append(msg_dict)
                continue

            # Simple string content
            result.append({"role": role, "content": _as_text(content)})
        return result

    def build_completed_content(self, raw_response: Any) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        choice = raw_response.choices[0] if raw_response.choices else None
        if choice is None:
            return blocks

        message = choice.message
        if message.content:
            blocks.append(ContentBlock(type="text", text=message.content))
        if message.tool_calls:
            for tc in message.tool_calls:
                blocks.append(
                    ContentBlock(
                        type="tool_use",
                        tool_id=tc.id,
                        tool_name=tc.function.name,
                        tool_input=json.loads(tc.function.arguments)
                        if tc.function.arguments
                        else {},
                    )
                )
        return blocks


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


_PROVIDERS_WITHOUT_DOCUMENT_BLOCKS: frozenset[str] = frozenset({
    "minimax",
    "openai",
    "gemini",
    "qwen",
})


def get_adapter(config: LLMConfig) -> AnthropicAdapter | OpenAIAdapter:
    """Create the appropriate adapter for a resolved LLM config."""
    if config.provider in ("anthropic", "minimax"):
        base_url: str | None = config.base_url or PROVIDER_BASE_URLS.get(config.provider)
        supports_docs: bool = config.provider not in _PROVIDERS_WITHOUT_DOCUMENT_BLOCKS
        return AnthropicAdapter(
            api_key=config.api_key,
            base_url=base_url,
            supports_document_blocks=supports_docs,
        )

    if config.provider in ("openai", "gemini", "qwen"):
        base_url = config.base_url or PROVIDER_BASE_URLS.get(config.provider)
        supports_docs: bool = config.provider not in _PROVIDERS_WITHOUT_DOCUMENT_BLOCKS
        return OpenAIAdapter(
            api_key=config.api_key,
            base_url=base_url,
            supports_document_blocks=supports_docs,
        )

    raise ValueError(f"Unsupported LLM provider: {config.provider}")

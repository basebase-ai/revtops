"""
Provider-agnostic LLM adapter layer.

Two adapter implementations cover four providers:
- AnthropicAdapter: Anthropic (native) + MiniMax (base_url override)
- OpenAIAdapter: OpenAI (native) + Gemini (base_url override)

Both yield a common StreamEvent protocol so the orchestrator and services
are decoupled from vendor-specific SDK details.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol

from anthropic import APIStatusError as AnthropicAPIStatusError, AsyncAnthropic
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Common types
# ---------------------------------------------------------------------------

LLMProvider = Literal["anthropic", "minimax", "openai", "gemini"]

PROVIDER_BASE_URLS: dict[str, str] = {
    "minimax": "https://api.minimax.io/anthropic",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}

PROVIDER_DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "anthropic": {"primary": "claude-opus-4-6", "cheap": "claude-haiku-4-5-20251001"},
    "minimax": {"primary": "MiniMax-M2.7", "cheap": "MiniMax-M2.7-highspeed"},
    "openai": {"primary": "gpt-5", "cheap": "gpt-5-mini"},
    "gemini": {"primary": "gemini-2.5-pro", "cheap": "gemini-2.5-flash"},
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

    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url is not None:
            kwargs["base_url"] = base_url
        self._client: AsyncAnthropic = AsyncAnthropic(**kwargs)

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
        api_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
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
        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
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
        return messages

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

    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url is not None:
            kwargs["base_url"] = base_url
        self._client: AsyncOpenAI = AsyncOpenAI(**kwargs)

    def _build_token_limit_kwargs(self, *, model: str, max_tokens: int) -> dict[str, int]:
        """Map token limit parameter name based on OpenAI model requirements."""
        # Newer reasoning families (e.g. gpt-5 / o-series) reject `max_tokens`.
        normalized_model: str = model.strip().lower().split("/")[-1]
        uses_completion_tokens: bool = normalized_model.startswith(("gpt-5", "o"))
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
            "model": model,
            "messages": api_messages,
            "stream": True,
        }
        api_kwargs.update(self._build_token_limit_kwargs(model=model, max_tokens=max_tokens))
        if tools:
            api_kwargs["tools"] = self.format_tools(tools)

        tool_calls_accum: dict[int, dict[str, str]] = {}
        current_text_started: bool = False

        stream = await self._client.chat.completions.create(**api_kwargs)
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

        response = await self._client.chat.completions.create(
            model=model,
            messages=api_messages,
            **self._build_token_limit_kwargs(model=model, max_tokens=max_tokens),
        )
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


def get_adapter(config: LLMConfig) -> AnthropicAdapter | OpenAIAdapter:
    """Create the appropriate adapter for a resolved LLM config."""
    if config.provider in ("anthropic", "minimax"):
        base_url: str | None = config.base_url or PROVIDER_BASE_URLS.get(config.provider)
        return AnthropicAdapter(api_key=config.api_key, base_url=base_url)

    if config.provider in ("openai", "gemini"):
        base_url = config.base_url or PROVIDER_BASE_URLS.get(config.provider)
        return OpenAIAdapter(api_key=config.api_key, base_url=base_url)

    raise ValueError(f"Unsupported LLM provider: {config.provider}")

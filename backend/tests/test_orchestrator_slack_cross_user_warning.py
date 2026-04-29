import asyncio
import json
from types import SimpleNamespace

from agents.orchestrator import ChatOrchestrator
from services.llm_adapter import StreamEvent


class _FakeAdapter:
    def __init__(self) -> None:
        self._calls = 0

    async def stream(self, **kwargs):  # type: ignore[no-untyped-def]
        self._calls += 1
        if self._calls == 1:
            yield StreamEvent(type="tool_use_start", tool_id="tool-1", tool_name="query_on_connector")
            yield StreamEvent(type="tool_input_delta", tool_input_json='{"connector":"hubspot","query":"x"}')
            yield StreamEvent(type="tool_use_stop")
            return

        yield StreamEvent(type="text_delta", text="Final response")


async def _collect_stream(orchestrator: ChatOrchestrator) -> list[str]:
    messages = [{"role": "user", "content": "hi"}]
    content_blocks: list[dict[str, object]] = []
    out: list[str] = []
    async for chunk in orchestrator._stream_with_tools(messages, "sys", content_blocks, "fake-model"):
        out.append(chunk)
    return out


def test_stream_with_tools_emits_cross_user_warning_for_slack_sources(monkeypatch) -> None:
    orchestrator = ChatOrchestrator(
        user_id="u1",
        organization_id="org1",
        source="slack_thread",
    )
    orchestrator._adapter = _FakeAdapter()
    orchestrator._llm_config = SimpleNamespace(provider="openai")

    async def _fake_execute_tool(*args, **kwargs):  # type: ignore[no-untyped-def]
        return {"warning": "Used teammate connector", "status": "success"}

    monkeypatch.setattr("agents.orchestrator.execute_tool", _fake_execute_tool)

    chunks = asyncio.run(_collect_stream(orchestrator))
    joined = "".join(chunks)

    assert "⚠️ Used teammate connector" in joined
    assert "Final response" in joined


def test_stream_with_tools_does_not_emit_warning_for_web(monkeypatch) -> None:
    orchestrator = ChatOrchestrator(
        user_id="u1",
        organization_id="org1",
        source="web",
    )
    orchestrator._adapter = _FakeAdapter()
    orchestrator._llm_config = SimpleNamespace(provider="openai")

    async def _fake_execute_tool(*args, **kwargs):  # type: ignore[no-untyped-def]
        return {"warning": "Used teammate connector", "status": "success"}

    monkeypatch.setattr("agents.orchestrator.execute_tool", _fake_execute_tool)

    chunks = asyncio.run(_collect_stream(orchestrator))
    joined = "".join(chunks)

    assert "⚠️ Used teammate connector" not in joined
    assert "Final response" in joined

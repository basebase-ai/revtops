from __future__ import annotations

import asyncio
import time
from typing import Any

from api import websockets
from services import task_manager as task_manager_module
from services.task_manager import TaskManager


class _FakeSocket:
    def __init__(self, delay_seconds: float = 0.0, should_fail: bool = False) -> None:
        self.delay_seconds = delay_seconds
        self.should_fail = should_fail
        self.messages: list[str] = []

    async def send_text(self, message: str) -> None:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.should_fail:
            raise RuntimeError("socket closed")
        self.messages.append(message)


def test_fanout_message_sends_concurrently() -> None:
    fast = _FakeSocket()
    slow = _FakeSocket(delay_seconds=0.2)

    start = time.perf_counter()
    dead = asyncio.run(websockets._fanout_message({fast, slow}, '{"ok": true}'))
    elapsed = time.perf_counter() - start

    assert dead == set()
    assert len(fast.messages) == 1
    assert len(slow.messages) == 1
    assert elapsed < 0.35


def test_task_manager_broadcast_does_not_block_on_stalled_socket() -> None:
    manager = TaskManager()
    task_id = "task-for-fanout-test"
    fast = _FakeSocket()
    stalled = _FakeSocket(delay_seconds=5.0)

    async def _run() -> float:
        async with manager._lock:
            manager._subscriptions[task_id] = {fast, stalled}  # noqa: SLF001

        start = time.perf_counter()
        await manager._broadcast(task_id, {"event": "tick"})  # noqa: SLF001
        elapsed_inner = time.perf_counter() - start

        async with manager._lock:
            remaining = manager._subscriptions[task_id]  # noqa: SLF001

        assert fast in remaining
        assert stalled not in remaining
        return elapsed_inner

    elapsed = asyncio.run(_run())
    assert elapsed < 2.2
    assert len(fast.messages) == 1


def test_task_manager_broadcast_snapshots_message_before_session_close(monkeypatch: Any) -> None:
    manager = TaskManager()
    detached = {"value": False}
    captured_payloads: list[dict[str, Any]] = []

    class _FakeMessage:
        def to_dict(self) -> dict[str, Any]:
            if detached["value"]:
                raise RuntimeError("simulated detached instance access")
            return {"id": "assistant-msg-1", "role": "assistant", "content_blocks": []}

    class _FakeScalarResult:
        def __init__(self, value: Any) -> None:
            self._value = value

        def one_or_none(self) -> Any:
            return self._value

    class _FakeResult:
        def __init__(self, row: Any = None, scalar_value: Any = None) -> None:
            self._row = row
            self._scalar_value = scalar_value

        def one_or_none(self) -> Any:
            return self._row

        def scalars(self) -> _FakeScalarResult:
            return _FakeScalarResult(self._scalar_value)

    class _FakeSession:
        def __init__(self, result: _FakeResult) -> None:
            self._result = result

        async def execute(self, _query: Any) -> _FakeResult:
            return self._result

    class _FakeSessionCtx:
        def __init__(self, result: _FakeResult, mark_detached: bool = False) -> None:
            self._session = _FakeSession(result)
            self._mark_detached = mark_detached

        async def __aenter__(self) -> _FakeSession:
            return self._session

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            if self._mark_detached:
                detached["value"] = True
            return False

    contexts = [
        _FakeSessionCtx(_FakeResult(row=("shared", ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]))),
        _FakeSessionCtx(_FakeResult(scalar_value=_FakeMessage()), mark_detached=True),
    ]

    def _fake_get_session(*_args: Any, **_kwargs: Any) -> _FakeSessionCtx:
        return contexts.pop(0)

    async def _fake_broadcast(**kwargs: Any) -> None:
        captured_payloads.append(kwargs["message_data"])

    monkeypatch.setattr(task_manager_module, "get_session", _fake_get_session)
    monkeypatch.setattr(
        __import__("api.websockets", fromlist=["broadcast_conversation_message"]),
        "broadcast_conversation_message",
        _fake_broadcast,
    )

    async def _run() -> None:
        await manager._broadcast_assistant_message_to_participants(  # noqa: SLF001
            conversation_id="11111111-1111-1111-1111-111111111111",
            organization_id="22222222-2222-2222-2222-222222222222",
            exclude_user_id="33333333-3333-3333-3333-333333333333",
        )

    asyncio.run(_run())
    assert captured_payloads == [{"id": "assistant-msg-1", "role": "assistant", "content_blocks": []}]

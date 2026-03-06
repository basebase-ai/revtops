from __future__ import annotations

import asyncio
import time

from api import websockets
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

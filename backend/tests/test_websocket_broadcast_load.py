from __future__ import annotations

import asyncio
import time
from typing import Any

from api.websockets import (
    ConversationBroadcaster,
    SyncProgressBroadcaster,
    WEBSOCKET_SEND_TIMEOUT_SECONDS as API_WS_TIMEOUT,
)
from services.task_manager import TaskManager, WEBSOCKET_SEND_TIMEOUT_SECONDS as TASK_WS_TIMEOUT


class _FakeWebSocket:
    def __init__(self, *, delay_seconds: float = 0.0, should_fail: bool = False) -> None:
        self.delay_seconds = delay_seconds
        self.should_fail = should_fail
        self.messages: list[str] = []

    async def send_text(self, message: str) -> None:
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        if self.should_fail:
            raise RuntimeError("socket send failed")
        self.messages.append(message)


def test_sync_broadcaster_timeout_isolation_under_load() -> None:
    async def _run() -> None:
        broadcaster = SyncProgressBroadcaster()
        fast_ws = _FakeWebSocket()
        slow_ws = _FakeWebSocket(delay_seconds=5.0)

        broadcaster.register("org-1", fast_ws)
        broadcaster.register("org-1", slow_ws)

        started = time.perf_counter()
        await broadcaster.broadcast("org-1", "sync_progress", {"count": 1})
        elapsed = time.perf_counter() - started

        assert elapsed < API_WS_TIMEOUT + 0.7
        assert len(fast_ws.messages) == 1
        assert slow_ws not in broadcaster._connections["org-1"]

    asyncio.run(_run())


def test_conversation_broadcaster_timeout_isolation_under_load() -> None:
    async def _run() -> None:
        broadcaster = ConversationBroadcaster()
        fast_ws = _FakeWebSocket()
        slow_ws = _FakeWebSocket(delay_seconds=5.0)

        broadcaster.register("user-1", fast_ws)
        broadcaster.register("user-2", slow_ws)

        started = time.perf_counter()
        await broadcaster.broadcast_to_users(
            user_ids=["user-1", "user-2"],
            event_type="new_message",
            data={"conversation_id": "c1", "message": {"text": "hello"}},
        )
        elapsed = time.perf_counter() - started

        assert elapsed < API_WS_TIMEOUT + 0.7
        assert len(fast_ws.messages) == 1
        assert slow_ws not in broadcaster._user_connections["user-2"]

    asyncio.run(_run())


def test_task_manager_broadcast_timeout_isolation_under_load() -> None:
    async def _run() -> None:
        manager = TaskManager()
        fast_ws = _FakeWebSocket()
        slow_ws = _FakeWebSocket(delay_seconds=5.0)

        async with manager._lock:
            manager._subscriptions["task-1"] = {fast_ws, slow_ws}  # type: ignore[assignment]

        started = time.perf_counter()
        await manager._broadcast("task-1", {"type": "task_chunk", "chunk": {"index": 0}})
        elapsed = time.perf_counter() - started

        assert elapsed < TASK_WS_TIMEOUT + 0.7
        assert len(fast_ws.messages) == 1

        async with manager._lock:
            assert slow_ws not in manager._subscriptions["task-1"]

    asyncio.run(_run())

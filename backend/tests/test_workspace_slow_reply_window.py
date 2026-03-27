import asyncio
import time

import messengers._workspace as workspace_module
from messengers._workspace import WorkspaceMessenger
from messengers.base import MessengerMeta, ResponseMode


class _TestWorkspaceMessenger(WorkspaceMessenger):
    meta = MessengerMeta(
        name="Test",
        slug="test",
        response_mode=ResponseMode.STREAMING,
    )

    async def resolve_organization(self, user, message):  # type: ignore[override]
        raise NotImplementedError

    async def find_or_create_conversation(self, organization_id, user, message):  # type: ignore[override]
        raise NotImplementedError

    async def download_attachments(self, message):  # type: ignore[override]
        raise NotImplementedError

    def format_text(self, markdown: str) -> str:
        return markdown

    async def post_message(
        self,
        channel_id: str,
        text: str,
        thread_id: str | None = None,
        *,
        workspace_id: str | None = None,
        organization_id: str | None = None,
    ) -> str | None:
        return "posted"


def test_wait_for_slow_reply_window_waits_and_rechecks_completion(monkeypatch) -> None:
    monkeypatch.setattr(workspace_module, "SLOW_REPLY_MIN_SECONDS_SINCE_LAST_MESSAGE", 0.2)
    monkeypatch.setattr(workspace_module, "SLOW_REPLY_RETRY_BACKOFF_SECONDS", 0.05)

    messenger = _TestWorkspaceMessenger()

    async def _run() -> float:
        task: asyncio.Task[int] = asyncio.create_task(asyncio.sleep(0.03, result=1))
        started_at = time.monotonic()
        await messenger._wait_for_slow_reply_window(
            response_task=task,
            get_last_message_sent_at=lambda: started_at,
        )
        elapsed = time.monotonic() - started_at
        assert task.done()
        return elapsed

    elapsed = asyncio.run(_run())
    assert elapsed >= 0.03


def test_wait_for_slow_reply_window_returns_immediately_when_no_prior_message() -> None:
    messenger = _TestWorkspaceMessenger()

    async def _run() -> float:
        task: asyncio.Task[int] = asyncio.create_task(asyncio.sleep(1.0, result=1))
        started_at = time.monotonic()
        await messenger._wait_for_slow_reply_window(
            response_task=task,
            get_last_message_sent_at=lambda: None,
        )
        elapsed = time.monotonic() - started_at
        task.cancel()
        return elapsed

    elapsed = asyncio.run(_run())
    assert elapsed < 0.1

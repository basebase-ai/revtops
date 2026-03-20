import asyncio
import json

from messengers._workspace import WorkspaceMessenger
from messengers.base import MessengerMeta, ResponseMode


class _TestWorkspaceMessenger(WorkspaceMessenger):
    meta = MessengerMeta(
        name="Test",
        slug="test",
        response_mode=ResponseMode.STREAMING,
    )

    def __init__(self) -> None:
        self.posted_messages: list[dict[str, str | None]] = []

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
        self.posted_messages.append(
            {
                "channel_id": channel_id,
                "text": text,
                "thread_id": thread_id,
                "workspace_id": workspace_id,
                "organization_id": organization_id,
            }
        )
        return "posted"


def test_handle_json_chunk_skips_duplicate_running_tool_status_messages() -> None:
    messenger = _TestWorkspaceMessenger()
    posted_tool_statuses: dict[str, tuple[str, str]] = {}
    chunk = json.dumps(
        {
            "type": "tool_call",
            "tool_id": "tool-123",
            "tool_name": "get_connector_docs",
            "status": "running",
            "status_text": "Reading Linear docs",
        }
    )

    async def _run() -> None:
        await messenger._handle_json_chunk(
            chunk,
            channel_id="C123",
            thread_id="thread-1",
            workspace_id="T123",
            organization_id="org-1",
            posted_tool_statuses=posted_tool_statuses,
        )
        await asyncio.sleep(0)
        await messenger._handle_json_chunk(
            chunk,
            channel_id="C123",
            thread_id="thread-1",
            workspace_id="T123",
            organization_id="org-1",
            posted_tool_statuses=posted_tool_statuses,
        )
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert messenger.posted_messages == [
        {
            "channel_id": "C123",
            "text": "Reading Linear docs",
            "thread_id": "thread-1",
            "workspace_id": "T123",
            "organization_id": "org-1",
        }
    ]


def test_handle_json_chunk_allows_same_status_text_after_status_change() -> None:
    messenger = _TestWorkspaceMessenger()
    posted_tool_statuses: dict[str, tuple[str, str]] = {}
    running_chunk = json.dumps(
        {
            "type": "tool_call",
            "tool_id": "tool-456",
            "tool_name": "get_connector_docs",
            "status": "running",
            "status_text": "Reading Linear docs",
        }
    )
    complete_chunk = json.dumps(
        {
            "type": "tool_call",
            "tool_id": "tool-456",
            "tool_name": "get_connector_docs",
            "status": "complete",
            "status_text": "Reading Linear docs",
        }
    )

    async def _run() -> None:
        await messenger._handle_json_chunk(
            running_chunk,
            channel_id="C123",
            thread_id="thread-1",
            workspace_id="T123",
            organization_id="org-1",
            posted_tool_statuses=posted_tool_statuses,
        )
        await asyncio.sleep(0)
        await messenger._handle_json_chunk(
            complete_chunk,
            channel_id="C123",
            thread_id="thread-1",
            workspace_id="T123",
            organization_id="org-1",
            posted_tool_statuses=posted_tool_statuses,
        )
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert [message["text"] for message in messenger.posted_messages] == [
        "Reading Linear docs",
        "Reading Linear docs",
    ]

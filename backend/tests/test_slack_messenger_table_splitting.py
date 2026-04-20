import pytest

from messengers.slack import SlackMessenger, _split_markdown_for_slack_tables


def test_split_markdown_for_slack_tables_keeps_single_table_message() -> None:
    markdown = """
Summary

| Name | Value |
| --- | --- |
| A | 1 |
""".strip()

    assert _split_markdown_for_slack_tables(markdown) == [markdown]


def test_split_markdown_for_slack_tables_splits_multi_table_message() -> None:
    markdown = """
First table:
| Name | Value |
| --- | --- |
| A | 1 |

Second table:
| Team | Score |
| --- | --- |
| X | 99 |
""".strip()

    chunks = _split_markdown_for_slack_tables(markdown)

    assert len(chunks) == 4
    assert chunks[0] == "First table:"
    assert "Name | Value" in chunks[1]
    assert chunks[2] == "Second table:"
    assert "Team | Score" in chunks[3]


@pytest.mark.asyncio
async def test_format_and_post_splits_into_multiple_slack_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    messenger = SlackMessenger()
    posted: list[dict[str, object]] = []

    async def _fake_post_message(**kwargs):  # type: ignore[no-untyped-def]
        posted.append(kwargs)
        return "123.456"

    monkeypatch.setattr(messenger, "post_message", _fake_post_message)

    await messenger.format_and_post(
        channel_id="C123",
        thread_id="T123",
        text_to_send="""
First:
| Name | Value |
| --- | --- |
| A | 1 |

Second:
| Team | Score |
| --- | --- |
| X | 99 |
""".strip(),
    )

    assert len(posted) == 4
    # Exactly two messages should include Slack table blocks.
    assert sum(1 for call in posted if call.get("blocks")) == 2

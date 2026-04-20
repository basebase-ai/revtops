from connectors.slack import markdown_to_mrkdwn


def test_markdown_to_mrkdwn_uses_blocks_for_single_table() -> None:
    text, blocks = markdown_to_mrkdwn(
        """
Summary

| Name | Value |
| --- | --- |
| A | 1 |
| B | 2 |
""".strip()
    )

    assert blocks is not None
    assert len(blocks) == 1
    assert blocks[0]["type"] == "table"
    assert "Table: 2 rows × 2 columns" in text


def test_markdown_to_mrkdwn_falls_back_to_code_blocks_for_multiple_tables() -> None:
    text, blocks = markdown_to_mrkdwn(
        """
First table:
| Name | Value |
| --- | --- |
| A | 1 |

Second table:
| Team | Score |
| --- | --- |
| X | 99 |
""".strip()
    )

    assert blocks is None
    assert text.count("```") >= 4
    assert "Name | Value" in text
    assert "Team | Score" in text

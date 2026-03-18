"""Tests for Slack table formatting (slack_tables module)."""

from connectors.slack_tables import (
    format_markdown_table_inline,
    format_table_as_blocks,
    parse_markdown_table,
)


def test_parse_markdown_table_valid() -> None:
    md = "| A | B |\n| 1 | 2 |\n| 3 | 4 |"
    out = parse_markdown_table(md)
    assert out is not None
    cols, rows = out
    assert cols == ["A", "B"]
    assert rows == [{"A": "1", "B": "2"}, {"A": "3", "B": "4"}]


def test_parse_markdown_table_no_leading_pipes() -> None:
    md = "Name | Email | Phone\n--- | --- | ---\nAlice | alice@co.com | 555\nBob | bob@co.com | 666"
    out = parse_markdown_table(md)
    assert out is not None
    cols, rows = out
    assert cols == ["Name", "Email", "Phone"]
    assert len(rows) == 2
    assert rows[0]["Name"] == "Alice"
    assert rows[1]["Email"] == "bob@co.com"


def test_parse_markdown_table_strips_separator_row() -> None:
    md = "| Name | Amount |\n| --- | --- |\n| Acme | 100 |"
    out = parse_markdown_table(md)
    assert out is not None
    cols, rows = out
    assert cols == ["Name", "Amount"]
    assert rows == [{"Name": "Acme", "Amount": "100"}]


def test_parse_markdown_table_empty_returns_none() -> None:
    assert parse_markdown_table("") is None
    assert parse_markdown_table("\n\n") is None


def test_format_table_as_blocks_structure() -> None:
    columns: list[str] = ["Name", "Email"]
    rows: list[dict[str, str]] = [
        {"Name": "Alice", "Email": "alice@example.com"},
        {"Name": "Bob", "Email": "bob@example.com"},
    ]
    blocks = format_table_as_blocks(columns, rows)
    assert len(blocks) == 1
    table: dict = blocks[0]
    assert table["type"] == "table"
    assert len(table["rows"]) == 3  # header + 2 data rows
    assert table["rows"][0][0]["text"] == "Name"
    assert table["rows"][0][1]["text"] == "Email"
    assert table["rows"][1][0]["text"] == "Alice"
    assert table["rows"][1][1]["text"] == "alice@example.com"
    assert table["rows"][2][0]["text"] == "Bob"
    assert table["rows"][2][1]["text"] == "bob@example.com"
    assert all(cs.get("is_wrapped") is True for cs in table["column_settings"])


def test_format_inline_small_table_returns_blocks() -> None:
    md = "| X | Y |\n| a | b |"
    blocks, fallback = format_markdown_table_inline(md)
    assert blocks is not None
    assert len(blocks) == 1
    assert blocks[0]["type"] == "table"
    assert "Table" in fallback and "1" in fallback and "2" in fallback


def test_format_inline_medium_table_returns_blocks() -> None:
    md = (
        "| Name | Title | Email | Phone |\n"
        "| --- | --- | --- | --- |\n"
        "| Jon Alferness | CEO | jon@basebase.com | +1 (415) 596-7768 |\n"
        "| Teg Grenager | Head of Engineering | teg@basebase.com | +1 (415) 902-8648 |"
    )
    blocks, fallback = format_markdown_table_inline(md)
    assert blocks is not None
    assert blocks[0]["type"] == "table"
    assert len(blocks[0]["rows"]) == 3  # header + 2 data
    assert blocks[0]["rows"][0][0]["text"] == "Name"
    assert "Table" in fallback and "2" in fallback and "4" in fallback


def test_format_inline_fallback_on_bad_input() -> None:
    blocks, fallback = format_markdown_table_inline("not a table at all")
    assert blocks is None
    assert fallback.startswith("```")
    assert "not a table" in fallback

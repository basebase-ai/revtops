"""Tests for Slack table formatting (slack_tables module)."""

import pytest

from connectors.slack_tables import (
    format_table_for_slack,
    format_markdown_table_inline,
    parse_markdown_table,
)


def test_parse_markdown_table_valid() -> None:
    md = "| A | B |\n| 1 | 2 |\n| 3 | 4 |"
    out = parse_markdown_table(md)
    assert out is not None
    cols, rows = out
    assert cols == ["A", "B"]
    assert rows == [{"A": "1", "B": "2"}, {"A": "3", "B": "4"}]


def test_parse_markdown_table_with_separator_removed() -> None:
    md = "| Name | Amount |\n| Acme | 100 |"
    out = parse_markdown_table(md)
    assert out is not None
    cols, rows = out
    assert cols == ["Name", "Amount"]
    assert rows == [{"Name": "Acme", "Amount": "100"}]


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


def test_format_markdown_table_inline_returns_code_block() -> None:
    md = "| X | Y |\n| a | b |"
    result = format_markdown_table_inline(md)
    assert result.startswith("```")
    assert result.endswith("```")
    assert "X" in result and "Y" in result


def test_format_table_for_slack_tiny_returns_blocks_and_text() -> None:
    columns = ["Name", "Amount"]
    rows = [{"Name": "Acme", "Amount": "10"}, {"Name": "Beta", "Amount": "20"}]
    payload = format_table_for_slack(columns, rows)
    assert "blocks" in payload
    assert "text" in payload
    assert payload["blocks"]
    assert payload["text"]


def test_format_table_for_slack_medium_returns_text_only() -> None:
    columns = ["A", "B", "C"]
    rows = [{"A": str(i), "B": str(i * 2), "C": str(i * 3)} for i in range(6)]
    payload = format_table_for_slack(columns, rows)
    assert "text" in payload
    assert payload["text"].startswith("```")
    assert "blocks" not in payload or payload.get("blocks") is None


def test_format_table_for_slack_large_returns_csv_file() -> None:
    columns = ["Col1", "Col2"]
    rows = [{"Col1": f"r{i}", "Col2": f"v{i}"} for i in range(15)]
    payload = format_table_for_slack(columns, rows)
    assert "csv_bytes" in payload
    assert "csv_filename" in payload
    assert "initial_comment" in payload
    assert payload["csv_bytes"]
    assert b"Col1" in payload["csv_bytes"]
    assert b"r0" in payload["csv_bytes"]

"""
Slack table formatting: adaptive strategies for tabular data in Slack.

- Tiny (1-3 rows, <=4 cols): Block Kit section with fields
- Medium (4-10 rows): Monospace code block with column truncation
- Large (>10 rows): Text summary + CSV file attachment
"""

import csv
import io
import re
from typing import Any, TypedDict

_SEPARATOR_RE: re.Pattern[str] = re.compile(r"^\|?[\s\-:|]+\|?$")

# Thresholds from plan
_TINY_MAX_ROWS: int = 3
_TINY_MAX_COLS: int = 4
_MEDIUM_MAX_ROWS: int = 10
_CODE_CELL_MAX_LEN: int = 20


class SlackTablePayload(TypedDict, total=False):
    """Payload for posting a table to Slack. Exactly one of (blocks+text), text_only, or file_upload is set."""

    blocks: list[dict[str, Any]]
    text: str
    initial_comment: str
    csv_bytes: bytes
    csv_filename: str


def _cell_str(value: Any) -> str:
    """Convert a cell value to a safe string for display or CSV."""
    if value is None:
        return ""
    s: str = str(value).strip()
    return s if s else ""


def _split_pipe_cells(line: str) -> list[str]:
    """Split a pipe-delimited table row into cell values.

    Handles both ``| a | b | c |`` and ``a | b | c`` formats.
    """
    stripped: str = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [c.strip() for c in stripped.split("|")]


def parse_markdown_table(md_table: str) -> tuple[list[str], list[dict[str, Any]]] | None:
    """
    Parse a markdown pipe table string into (columns, rows).
    Separator rows are automatically filtered out. Returns None if parse fails.
    """
    raw_lines: list[str] = [ln.strip() for ln in md_table.strip().split("\n") if ln.strip()]
    lines: list[str] = [ln for ln in raw_lines if not _SEPARATOR_RE.match(ln)]
    if not lines:
        return None

    columns: list[str] = _split_pipe_cells(lines[0])
    columns = [c for c in columns if c]
    if not columns:
        return None

    rows: list[dict[str, Any]] = []
    for line in lines[1:]:
        cells: list[str] = _split_pipe_cells(line)
        if len(cells) < len(columns):
            cells = cells + [""] * (len(columns) - len(cells))
        elif len(cells) > len(columns):
            cells = cells[: len(columns)]
        rows.append(dict(zip(columns, cells)))
    return (columns, rows)


def format_markdown_table_inline(md_table: str) -> str:
    """
    Format a markdown pipe table for Slack inline display.
    Separator rows are stripped automatically. Returns a truncated code block
    string, or the original wrapped in ``` if parsing fails.
    """
    parsed: tuple[list[str], list[dict[str, Any]]] | None = parse_markdown_table(md_table)
    if parsed is None:
        return "```\n" + md_table.strip() + "\n```"
    columns: list[str] = parsed[0]
    rows: list[dict[str, Any]] = parsed[1]
    return _format_as_codeblock(columns, rows)


def _format_as_fields(columns: list[str], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Format tiny table as Block Kit section(s): header text + fields (one field per row)."""
    header_parts: list[str] = [f"*{col}*" for col in columns]
    header_text: str = "  ".join(header_parts)

    field_texts: list[str] = []
    for row in rows:
        parts: list[str] = [_cell_str(row.get(col)) for col in columns]
        field_texts.append("  ".join(parts))

    # One section: header as text, rows as fields (max 10 fields in Slack)
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": header_text},
            "fields": [{"type": "mrkdwn", "text": t} for t in field_texts[:10]],
        }
    ]
    fallback_text: str = header_text + "\n" + "\n".join(field_texts)
    return blocks, fallback_text


def _format_as_codeblock(columns: list[str], rows: list[dict[str, Any]]) -> str:
    """Format medium table as a single monospace code block with truncated cells."""
    def truncate(s: str, max_len: int = _CODE_CELL_MAX_LEN) -> str:
        s = s.replace("\n", " ").strip()
        return (s[: max_len - 1] + "…") if len(s) > max_len else s

    cells: list[list[str]] = [[truncate(str(col)) for col in columns]]
    for row in rows:
        cells.append([truncate(_cell_str(row.get(col))) for col in columns])

    col_widths: list[int] = [max(len(cells[r][c]) for r in range(len(cells))) for c in range(len(columns))]
    lines: list[str] = []
    for i, row_cells in enumerate(cells):
        line: str = " | ".join(c.ljust(col_widths[j]) for j, c in enumerate(row_cells))
        lines.append(line)
    return "```\n" + "\n".join(lines) + "\n```"


def _format_as_csv(columns: list[str], rows: list[dict[str, Any]], filename: str = "data.csv") -> tuple[str, bytes]:
    """Produce summary text and CSV bytes for large tables."""
    buf: io.StringIO = io.StringIO()
    writer: csv.DictWriter = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: _cell_str(row.get(col)) for col in columns})
    csv_str: str = buf.getvalue()
    csv_bytes: bytes = csv_str.encode("utf-8")
    summary: str = f"{len(rows)} row(s) of data. See attached `{filename}`."
    return summary, csv_bytes


def format_table_for_slack(
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    csv_filename: str = "data.csv",
) -> SlackTablePayload:
    """
    Choose strategy by dimensions and return a SlackTablePayload.

    - Tiny (1-3 rows, <=4 cols): blocks + text (Block Kit section with fields)
    - Medium (4-10 rows): text only (truncated code block)
    - Large (>10 rows): initial_comment + csv_bytes + csv_filename
    """
    num_rows: int = len(rows)
    num_cols: int = len(columns)

    if num_rows <= _TINY_MAX_ROWS and num_cols <= _TINY_MAX_COLS and num_rows > 0:
        blocks, fallback = _format_as_fields(columns, rows)
        return SlackTablePayload(blocks=blocks, text=fallback)

    if num_rows <= _MEDIUM_MAX_ROWS:
        return SlackTablePayload(text=_format_as_codeblock(columns, rows))

    initial_comment, csv_bytes = _format_as_csv(columns, rows, filename=csv_filename)
    return SlackTablePayload(
        initial_comment=initial_comment,
        csv_bytes=csv_bytes,
        csv_filename=csv_filename,
    )

"""
Slack table formatting utilities for markdown_to_mrkdwn.

Parses markdown pipe tables and reformats them for Slack's narrow
monospace display.  The main entry point is ``format_markdown_table_inline``
which is called from ``markdown_to_mrkdwn`` in ``connectors/slack.py``.
"""

import re
from typing import Any

_SEPARATOR_RE: re.Pattern[str] = re.compile(r"^\|?[\s\-:|]+\|?$")

_SLACK_CODE_LINE_MAX: int = 44


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
    """Parse a markdown pipe table string into (columns, rows).

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


def _fits_in_codeblock(columns: list[str], rows: list[dict[str, Any]]) -> bool:
    """Check whether an aligned pipe table would fit within Slack's line width."""
    all_rows: list[list[str]] = [[str(c) for c in columns]]
    for row in rows:
        all_rows.append([str(row.get(col, "")) for col in columns])
    col_widths: list[int] = [
        max(len(all_rows[r][c]) for r in range(len(all_rows)))
        for c in range(len(columns))
    ]
    line_len: int = sum(col_widths) + 3 * (len(columns) - 1)
    return line_len <= _SLACK_CODE_LINE_MAX


def _format_aligned_table(columns: list[str], rows: list[dict[str, Any]]) -> str:
    """Aligned pipe table in a code block (only when it fits)."""
    all_cells: list[list[str]] = [[str(c) for c in columns]]
    for row in rows:
        all_cells.append([str(row.get(col, "")) for col in columns])
    col_widths: list[int] = [
        max(len(all_cells[r][c]) for r in range(len(all_cells)))
        for c in range(len(columns))
    ]
    lines: list[str] = []
    for row_cells in all_cells:
        line: str = " | ".join(cell.ljust(col_widths[j]) for j, cell in enumerate(row_cells))
        lines.append(line)
    return "```\n" + "\n".join(lines) + "\n```"


def _format_as_list(columns: list[str], rows: list[dict[str, Any]]) -> str:
    """Format each row as a bold-label list, which never wraps in Slack."""
    parts: list[str] = []
    for i, row in enumerate(rows):
        lines: list[str] = []
        for col in columns:
            val: str = str(row.get(col, "") or "—")
            lines.append(f"*{col}:* {val}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def format_markdown_table_inline(md_table: str) -> str:
    """Format a markdown pipe table for Slack inline display.

    - If the table fits in ~68 chars wide, use an aligned code block.
    - Otherwise, format each row as a labeled list (no wrapping).
    - Falls back to raw code block if parsing fails.
    """
    parsed: tuple[list[str], list[dict[str, Any]]] | None = parse_markdown_table(md_table)
    if parsed is None:
        return "```\n" + md_table.strip() + "\n```"
    columns: list[str] = parsed[0]
    rows: list[dict[str, Any]] = parsed[1]
    if not rows:
        return "```\n" + md_table.strip() + "\n```"
    if _fits_in_codeblock(columns, rows):
        return _format_aligned_table(columns, rows)
    return _format_as_list(columns, rows)

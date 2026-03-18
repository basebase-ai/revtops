"""
Slack table formatting: parse markdown pipe tables and convert to Block Kit
or fallback text for chat.postMessage.
"""

import re
from typing import Any

_SEPARATOR_RE: re.Pattern[str] = re.compile(r"^\|?[\s\-:|]+\|?$")

# Slack: max 10 fields per section, 50 blocks per message. Use Block Kit for small tables.
_BLOCK_KIT_MAX_ROWS: int = 15
_FIELDS_PER_SECTION_MAX: int = 10


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    s: str = str(value).strip()
    return s if s else "—"


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


def format_table_as_blocks(
    columns: list[str],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build Block Kit blocks for a table: header section, divider, one section per row.

    Slack section.fields are capped at 10; total blocks capped at 50.
    Caller should limit rows before calling (e.g. _BLOCK_KIT_MAX_ROWS).
    """
    num_cols: int = len(columns)
    if num_cols > _FIELDS_PER_SECTION_MAX:
        num_cols = _FIELDS_PER_SECTION_MAX
    cols_used: list[str] = columns[:num_cols]

    blocks: list[dict[str, Any]] = []

    # Header row
    header_fields: list[dict[str, str]] = [
        {"type": "mrkdwn", "text": f"*{col}*"} for col in cols_used
    ]
    blocks.append({"type": "section", "fields": header_fields})
    blocks.append({"type": "divider"})

    for row in rows:
        row_fields: list[dict[str, str]] = [
            {"type": "mrkdwn", "text": _cell_str(row.get(col))} for col in cols_used
        ]
        blocks.append({"type": "section", "fields": row_fields})

    return blocks


def _format_as_codeblock_fallback(columns: list[str], rows: list[dict[str, Any]]) -> str:
    """Fallback: aligned pipe table in a code block when Block Kit is not used."""
    all_cells: list[list[str]] = [[str(c) for c in columns]]
    for row in rows:
        all_cells.append([_cell_str(row.get(col)) for col in columns])
    col_widths: list[int] = [
        max(len(all_cells[r][c]) for r in range(len(all_cells)))
        for c in range(len(columns))
    ]
    lines: list[str] = [
        " | ".join(cell.ljust(col_widths[j]) for j, cell in enumerate(row_cells))
        for row_cells in all_cells
    ]
    return "```\n" + "\n".join(lines) + "\n```"


def format_markdown_table_inline(md_table: str) -> tuple[list[dict[str, Any]] | None, str]:
    """Format a markdown pipe table for Slack.

    Returns (blocks, fallback_text). When the table fits Block Kit limits,
    blocks is a list of Block Kit dicts and fallback_text is a short summary.
    When it does not fit or parse fails, blocks is None and fallback_text
    is the code-block or raw table string.
    """
    parsed: tuple[list[str], list[dict[str, Any]]] | None = parse_markdown_table(md_table)
    if parsed is None:
        return (None, "```\n" + md_table.strip() + "\n```")
    columns: list[str] = parsed[0]
    rows: list[dict[str, Any]] = parsed[1]
    if not rows:
        return (None, "```\n" + md_table.strip() + "\n```")

    if len(rows) <= _BLOCK_KIT_MAX_ROWS and len(columns) <= _FIELDS_PER_SECTION_MAX:
        blocks = format_table_as_blocks(columns, rows)
        if len(blocks) <= 50:
            fallback: str = f"Table: {len(rows)} rows × {len(columns)} columns"
            return (blocks, fallback)

    return (None, _format_as_codeblock_fallback(columns, rows))

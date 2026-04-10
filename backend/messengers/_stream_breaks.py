from __future__ import annotations

import re
from typing import Literal

StreamBreakStrategy = Literal["best", "quickest_safe"]

_SENTENCE_BREAK_RE: re.Pattern[str] = re.compile(r"[.!?](?:\s|$)")
_FENCE_RE: re.Pattern[str] = re.compile(r"^```", re.MULTILINE)
_PIPE_TABLE_LINE_RE: re.Pattern[str] = re.compile(r"\|.+\||[^|\n]+(?:\|[^|\n]+){2,}")
_TITLE_ABBREVIATIONS: set[str] = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "st",
    "vs",
    "saint",
}


def _build_fence_ranges(text: str) -> list[tuple[int, int]]:
    """Return (start, end) ranges for fenced code blocks in *text*.

    Unpaired opening fences extend to the end of the string so that we
    never break inside an in-progress code block.
    """
    fences: list[int] = [m.start() for m in _FENCE_RE.finditer(text)]
    ranges: list[tuple[int, int]] = []
    i: int = 0
    while i < len(fences):
        open_pos: int = fences[i]
        close_pos: int = fences[i + 1] if i + 1 < len(fences) else len(text)
        ranges.append((open_pos, close_pos))
        i += 2
    return ranges


def _inside_code_fence(position: int, ranges: list[tuple[int, int]]) -> bool:
    """Return True if *position* falls inside any fenced code block range."""
    for start, end in ranges:
        if start <= position <= end:
            return True
    return False


def _is_valid_sentence_break(text: str, punct_idx: int) -> bool:
    """Return whether punctuation index is safe to break on."""
    if punct_idx >= 2 and text[punct_idx - 2:punct_idx] in {"'s", "'S"}:
        return False
    if punct_idx >= 2 and text[punct_idx - 2:punct_idx] == "**":
        return False
    if punct_idx >= 1 and text[punct_idx - 1:punct_idx] == "~":
        return False

    if text[punct_idx] == ".":
        token_match: re.Match[str] | None = re.search(r"([A-Za-z]+)$", text[:punct_idx])
        if token_match and token_match.group(1).lower() in _TITLE_ABBREVIATIONS:
            return False

    line_start: int = text.rfind("\n", 0, punct_idx) + 1
    line_end: int = text.find("\n", punct_idx)
    full_line: str = text[line_start:line_end] if line_end >= 0 else text[line_start:]
    line_prefix: str = text[line_start:punct_idx].strip()

    if line_prefix.startswith(("-", "*", "+")):
        return False
    if re.fullmatch(r"\d+", line_prefix):
        return False

    if "|" in full_line:
        return False

    if text[punct_idx] == ".":
        after: str = text[punct_idx + 1: punct_idx + 5]
        if re.match(r"[a-z]{2,4}\b", after, re.IGNORECASE):
            before_char: str = text[punct_idx - 1] if punct_idx >= 1 else ""
            if before_char.isalpha():
                return False

    return True


def _ends_inside_pipe_table(text: str) -> bool:
    """Return True if *text* ends in the middle of a markdown pipe table.

    A pipe table is a contiguous block of lines matching ``| ... |``.
    If the last non-blank line is a pipe-table row and there is no blank line
    or non-table text after it, assume more table rows are coming.
    """
    stripped: str = text.rstrip()
    if not stripped:
        return False
    last_newline: int = stripped.rfind("\n")
    last_line: str = stripped[last_newline + 1:].strip() if last_newline >= 0 else stripped.strip()
    return bool(_PIPE_TABLE_LINE_RE.fullmatch(last_line))


def find_safe_break(
    text: str,
    *,
    strategy: StreamBreakStrategy = "best",
    limit: int | None = None,
) -> int:
    """Find a safe break index for streamed/segmented text.

    - ``best``: choose the farthest safe sentence break within ``limit``.
    - ``quickest_safe``: choose the first safe sentence break within ``limit``.

    Breaks inside fenced code blocks (````` ``` `````) are always skipped.
    """
    if not text:
        return 0

    max_index: int = len(text) if limit is None else min(limit, len(text))
    if max_index <= 0:
        return 0

    if _ends_inside_pipe_table(text):
        return 0

    fence_ranges: list[tuple[int, int]] = _build_fence_ranges(text)

    selected_break: int = 0
    for match in _SENTENCE_BREAK_RE.finditer(text):
        candidate: int = match.end()
        if candidate > max_index:
            break
        punct_idx: int = match.start()
        if _inside_code_fence(punct_idx, fence_ranges):
            continue
        if not _is_valid_sentence_break(text, punct_idx):
            continue
        if strategy == "quickest_safe":
            return candidate
        selected_break = candidate

    if selected_break > 0:
        return selected_break

    # For unbounded streaming buffers, only sentence-safe boundaries are used.
    if limit is None:
        return 0

    # Bounded windows still require sentence boundaries.
    return 0

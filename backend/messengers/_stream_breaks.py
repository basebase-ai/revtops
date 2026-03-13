from __future__ import annotations

import re
from typing import Literal

StreamBreakStrategy = Literal["best", "quickest_safe"]

_SENTENCE_BREAK_RE: re.Pattern[str] = re.compile(r"[.!?](?:\s|$)")


def _is_valid_sentence_break(text: str, punct_idx: int) -> bool:
    """Return whether punctuation index is safe to break on."""
    if punct_idx >= 2 and text[punct_idx - 2:punct_idx] in {"'s", "'S"}:
        return False
    if punct_idx >= 2 and text[punct_idx - 2:punct_idx] == "**":
        return False
    if punct_idx >= 1 and text[punct_idx - 1:punct_idx] == "~":
        return False

    line_start: int = text.rfind("\n", 0, punct_idx) + 1
    line_prefix: str = text[line_start:punct_idx].strip()

    if line_prefix.startswith(("-", "*", "+")):
        return False
    if re.fullmatch(r"\d+", line_prefix):
        return False

    return True


def find_safe_break(
    text: str,
    *,
    strategy: StreamBreakStrategy = "best",
    limit: int | None = None,
) -> int:
    """Find a safe break index for streamed/segmented text.

    - ``best``: choose the farthest safe sentence break within ``limit``.
    - ``quickest_safe``: choose the first safe sentence break within ``limit``.
    """
    if not text:
        return 0

    max_index: int = len(text) if limit is None else min(limit, len(text))
    if max_index <= 0:
        return 0

    selected_break: int = 0
    for match in _SENTENCE_BREAK_RE.finditer(text):
        candidate: int = match.end()
        if candidate > max_index:
            break
        punct_idx: int = match.start()
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

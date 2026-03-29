"""Utilities for applying an automated-agent disclosure footer to outbound content."""

from __future__ import annotations

import re

AUTOMATED_AGENT_FOOTER: str = "Done by an automated agent via Basebase."
_AUTOMATED_AGENT_FOOTER_MARKER: re.Pattern[str] = re.compile(
    r"done\s+by\s+an\s+automated\s+agent",
    flags=re.IGNORECASE,
)


def ensure_automated_agent_footer(content: str | None) -> str:
    """Ensure outbound user-authored content includes an automation signature footer."""
    base_text: str = (content or "").rstrip()
    if _AUTOMATED_AGENT_FOOTER_MARKER.search(base_text):
        return base_text
    footer_line: str = f"— {AUTOMATED_AGENT_FOOTER}"
    if not base_text:
        return footer_line
    return f"{base_text}\n\n{footer_line}"

"""Utilities for selecting LLM models based on message characteristics."""

import re
from typing import Any

_SHORT_PHRASE_CANONICAL_RESPONSES: set[str] = {
    "yes", "yep", "yeah", "yup", "sure", "ok", "okay", "affirmative",
    "no", "nope", "nah", "negative",
    "thanks", "thank you", "thx", "ty", "thankyou",
}


def _normalize_short_phrase(text: str) -> str:
    """Normalize short text for semantic short-phrase checks."""
    collapsed = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return " ".join(collapsed.split())


def is_short_phrase_for_cheap_model(content: str | list[dict[str, Any]]) -> bool:
    """Return True if this turn is a 1-2 word yes/no/thanks-style phrase."""
    if isinstance(content, list):
        text_parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = " ".join(part for part in text_parts if part).strip()
    else:
        text = str(content).strip()

    if not text:
        return False

    normalized = _normalize_short_phrase(text)
    if not normalized:
        return False

    word_count = len(normalized.split())
    if word_count < 1 or word_count > 2:
        return False

    return normalized in _SHORT_PHRASE_CANONICAL_RESPONSES

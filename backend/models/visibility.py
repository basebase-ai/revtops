"""Shared visibility levels for artifacts and apps."""
from __future__ import annotations

from typing import Literal

VisibilityLevel = Literal["private", "team", "public"]

VALID_VISIBILITY_LEVELS: frozenset[str] = frozenset({"private", "team", "public"})


def normalize_visibility(value: str | None, *, default: str = "team") -> str:
    """Return a valid visibility string or default."""
    if value is None or value == "":
        return default
    s: str = value.strip().lower()
    if s in VALID_VISIBILITY_LEVELS:
        return s
    return default

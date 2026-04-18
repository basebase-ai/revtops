from __future__ import annotations

from pathlib import Path


def test_chat_scope_pill_remains_toggle_control() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    chat_component = repo_root / "frontend/src/components/Chat.tsx"
    source = chat_component.read_text(encoding="utf-8")

    assert "Scope: clickable pill toggle for conversation creator" in source
    assert "Shared with team — click to make private" in source
    assert "Private — click to share with team" in source
    assert "void handleMakePrivate();" in source
    assert "void handleMakeShared();" in source

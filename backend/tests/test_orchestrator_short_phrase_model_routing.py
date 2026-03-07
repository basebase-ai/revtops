from agents import orchestrator


def test_is_short_yes_no_thanks_phrase_detects_supported_variants() -> None:
    assert orchestrator._is_short_yes_no_thanks_phrase("yes")
    assert orchestrator._is_short_yes_no_thanks_phrase("yep")
    assert orchestrator._is_short_yes_no_thanks_phrase("nope")
    assert orchestrator._is_short_yes_no_thanks_phrase("thank you")
    assert orchestrator._is_short_yes_no_thanks_phrase("thx")


def test_is_short_yes_no_thanks_phrase_rejects_non_matching_inputs() -> None:
    assert not orchestrator._is_short_yes_no_thanks_phrase("")
    assert not orchestrator._is_short_yes_no_thanks_phrase("sounds good")
    assert not orchestrator._is_short_yes_no_thanks_phrase("yes please thanks")


def test_select_anthropic_model_for_turn_prefers_cheap_model_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator.settings, "USE_CHEAP_MODELS_FOR_SHORT_PHRASE", True)
    monkeypatch.setattr(orchestrator.settings, "ANTHROPIC_CHEAP_MODEL", "claude-3-5-haiku-20241022")
    monkeypatch.setattr(orchestrator.settings, "ANTHROPIC_PRIMARY_MODEL", "claude-opus-4-6")

    model = orchestrator._select_anthropic_model_for_turn("thank you")

    assert model == "claude-3-5-haiku-20241022"


def test_select_anthropic_model_for_turn_uses_primary_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator.settings, "USE_CHEAP_MODELS_FOR_SHORT_PHRASE", False)
    monkeypatch.setattr(orchestrator.settings, "ANTHROPIC_CHEAP_MODEL", "claude-3-5-haiku-20241022")
    monkeypatch.setattr(orchestrator.settings, "ANTHROPIC_PRIMARY_MODEL", "claude-opus-4-6")

    model = orchestrator._select_anthropic_model_for_turn("yes")

    assert model == "claude-opus-4-6"

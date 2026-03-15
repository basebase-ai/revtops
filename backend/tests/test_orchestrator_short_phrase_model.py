from agents.model_routing import is_short_phrase_for_cheap_model


def test_short_phrase_detector_accepts_yes_no_and_thanks_semantics() -> None:
    assert is_short_phrase_for_cheap_model("yes") is True
    assert is_short_phrase_for_cheap_model("Nope") is True
    assert is_short_phrase_for_cheap_model("thank you") is True
    assert is_short_phrase_for_cheap_model("thx!") is True


def test_short_phrase_detector_rejects_longer_or_non_target_content() -> None:
    assert is_short_phrase_for_cheap_model("sounds good") is False
    assert is_short_phrase_for_cheap_model("yes please") is False
    assert is_short_phrase_for_cheap_model("this is definitely longer") is False


def test_short_phrase_detector_handles_content_blocks() -> None:
    assert is_short_phrase_for_cheap_model([
        {"type": "text", "text": "Thanks"},
    ]) is True
    assert is_short_phrase_for_cheap_model([
        {"type": "tool_result", "content": "ignored"},
    ]) is False

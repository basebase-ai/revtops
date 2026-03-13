from messengers._stream_breaks import find_safe_break


def test_find_safe_stream_break_best_prefers_farthest_sentence_boundary() -> None:
    text = "First sentence. Second sentence? Third sentence"
    assert find_safe_break(text, strategy="best") == len("First sentence. Second sentence? ")


def test_find_safe_stream_break_quickest_safe_returns_earliest_boundary() -> None:
    text = "First sentence. Second sentence? Third sentence"
    assert find_safe_break(text, strategy="quickest_safe") == len("First sentence. ")


def test_find_safe_stream_break_skips_apostrophe_s_boundary() -> None:
    text = "The user's. request is queued"
    assert find_safe_break(text, strategy="best") == 0


def test_find_safe_stream_break_skips_formatting_mark_boundaries() -> None:
    assert find_safe_break("Wrapped in **. bold", strategy="best") == 0
    assert find_safe_break("Wrapped in ~. strike", strategy="best") == 0


def test_find_safe_stream_break_skips_bullet_boundaries() -> None:
    assert find_safe_break("- Item one. Item two", strategy="best") == 0
    assert find_safe_break("* Item one. Item two", strategy="best") == 0
    assert find_safe_break("+ Item one. Item two", strategy="best") == 0


def test_find_safe_stream_break_skips_numbered_list_boundaries() -> None:
    assert find_safe_break("1. First item", strategy="best") == 0


def test_find_safe_stream_break_never_uses_space_fallback_with_limit() -> None:
    assert find_safe_break("no sentence break here", strategy="best", limit=10) == 0

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


def test_find_safe_stream_break_skips_common_title_abbreviations() -> None:
    text = "I met Dr. Smith yesterday. We talked"
    assert find_safe_break(text, strategy="quickest_safe") == len("I met Dr. Smith yesterday. ")


def test_find_safe_stream_break_skips_saint_abbreviation() -> None:
    text = "They visited St. Louis last week. It was great"
    assert find_safe_break(text, strategy="quickest_safe") == len("They visited St. Louis last week. ")


def test_find_safe_stream_break_skips_vs_abbreviation() -> None:
    text = "This is a Lakers vs. Celtics preview. Tip-off at seven"
    assert find_safe_break(text, strategy="quickest_safe") == len("This is a Lakers vs. Celtics preview. ")


def test_find_safe_stream_break_skips_period_inside_uri() -> None:
    text = "Link https://example.com/path.to/file and then continue"
    assert find_safe_break(text, strategy="quickest_safe") == 0


def test_find_safe_stream_break_skips_question_mark_inside_uri() -> None:
    text = "Use https://example.com/search?q=test? value now"
    assert find_safe_break(text, strategy="quickest_safe") == 0


def test_find_safe_stream_break_defers_inside_pipe_table_with_pipes() -> None:
    text = "Here is the data:\n\n| Name | Email |\n| Alice | alice@co.com |"
    assert find_safe_break(text, strategy="quickest_safe") == 0


def test_find_safe_stream_break_defers_inside_pipe_table_without_pipes() -> None:
    text = "Here is the data:\n\nName | Email | Phone\n--- | --- | ---\nAlice | alice@co.com | 555"
    assert find_safe_break(text, strategy="quickest_safe") == 0


def test_find_safe_stream_break_allows_break_after_table_ends() -> None:
    text = "| Name | Email |\n| Alice | alice@co.com |\n\nNote: table done. More text"
    idx: int = find_safe_break(text, strategy="quickest_safe")
    assert idx > 0

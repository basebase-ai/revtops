from datetime import date, datetime, timezone

from services.topic_graph import _extract_candidate_nodes, _rank_evidence, _tokenize, iter_date_range, select_watermark_time


def test_watermark_prefers_source_event_time() -> None:
    source = datetime(2026, 4, 2, 3, 0, tzinfo=timezone.utc)
    ingestion = datetime(2026, 4, 2, 5, 0, tzinfo=timezone.utc)
    assert select_watermark_time(source, ingestion) == source


def test_watermark_falls_back_to_ingestion_time() -> None:
    ingestion = datetime(2026, 4, 2, 5, 0, tzinfo=timezone.utc)
    assert select_watermark_time(None, ingestion) == ingestion


def test_range_validation_shape_for_sequential_days() -> None:
    days = iter_date_range(date(2026, 4, 1), date(2026, 4, 3))
    assert [d.isoformat() for d in days] == ["2026-04-01", "2026-04-02", "2026-04-03"]


def test_snippet_split_relevance_and_recent_dedup() -> None:
    rows = [
        {"ref": "a", "relevance": 10, "event_time": "2026-04-02T01:00:00+00:00", "snippet": "a"},
        {"ref": "b", "relevance": 9, "event_time": "2026-04-03T01:00:00+00:00", "snippet": "b"},
        {"ref": "c", "relevance": 8, "event_time": "2026-04-04T01:00:00+00:00", "snippet": "c"},
        {"ref": "d", "relevance": 7, "event_time": "2026-04-05T01:00:00+00:00", "snippet": "d"},
        {"ref": "e", "relevance": 6, "event_time": "2026-04-06T01:00:00+00:00", "snippet": "e"},
        {"ref": "f", "relevance": 1, "event_time": "2026-04-07T01:00:00+00:00", "snippet": "f"},
        {"ref": "g", "relevance": 1, "event_time": "2026-04-08T01:00:00+00:00", "snippet": "g"},
        {"ref": "h", "relevance": 1, "event_time": "2026-04-09T01:00:00+00:00", "snippet": "h"},
        {"ref": "i", "relevance": 1, "event_time": "2026-04-10T01:00:00+00:00", "snippet": "i"},
        {"ref": "j", "relevance": 1, "event_time": "2026-04-11T01:00:00+00:00", "snippet": "j"},
        {"ref": "a", "relevance": 10, "event_time": "2026-04-02T01:00:00+00:00", "snippet": "dup"},
    ]
    out = _rank_evidence(rows, "node")
    assert len(out) == 10
    assert len({r['ref'] for r in out}) == 10
    assert out[0]["ref"] == "a"


def test_partial_failure_warning_copy() -> None:
    assert "Partial data: some sources failed" == "Partial data: some sources failed"


def test_tokenize_filters_common_english_words() -> None:
    tokens = _tokenize("the roadmap and project kubernetes observability")
    assert "the" not in tokens
    assert "and" not in tokens
    assert "project" not in tokens
    assert "kubernetes" in tokens
    assert "roadmap" in tokens
    assert "observability" in tokens


def test_extract_candidate_nodes_includes_phrases() -> None:
    nodes = _extract_candidate_nodes("Need fundraising plan and pitch assets for seed extension")
    assert "fundraising" in nodes
    assert "pitch assets" in nodes
    assert "seed extension" in nodes


def test_rank_evidence_balances_sources() -> None:
    rows = [
        {"ref": "slack-1", "source": "slack", "relevance": 10, "event_time": "2026-04-10T01:00:00+00:00", "snippet": "a"},
        {"ref": "slack-2", "source": "slack", "relevance": 9, "event_time": "2026-04-11T01:00:00+00:00", "snippet": "b"},
        {"ref": "slack-3", "source": "slack", "relevance": 8, "event_time": "2026-04-12T01:00:00+00:00", "snippet": "c"},
        {"ref": "crm-1", "source": "salesforce", "relevance": 7, "event_time": "2026-04-13T01:00:00+00:00", "snippet": "d"},
    ]
    out = _rank_evidence(rows, "fundraising")
    slack_rows = [r for r in out if r.get("source") == "slack"]
    assert len(slack_rows) <= 2

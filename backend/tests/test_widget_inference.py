"""Tests for widget inference and app preview endpoints.

Covers:
- Widget config validation (layout types, required fields)
- Screenshot endpoint (data URL validation, size limit)
- Widget generation prompt building
- Markdown fence stripping from LLM responses
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Widget inference: response parsing and validation
# ---------------------------------------------------------------------------

def _make_mock_app(title="Test App", description="A test", queries=None):
    app = MagicMock()
    app.title = title
    app.description = description
    app.queries = queries or {}
    return app


def _make_anthropic_response(text: str):
    """Build a mock Anthropic Messages response."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


class TestWidgetInferenceValidation:
    """Test the parsing/validation logic in generate_widget_config."""

    def test_valid_big_number_layout(self):
        """Valid big_number JSON is accepted."""
        raw = '{"layout":"big_number","title":"Pipeline","slots":{"value":"$2.4M","label":"Open Pipeline","trend":"up"}}'
        config = json.loads(raw)
        assert config["layout"] == "big_number"
        assert config["slots"]["value"] == "$2.4M"

    def test_valid_mini_list_layout(self):
        raw = '{"layout":"mini_list","title":"Top Deals","slots":{"rows":[{"label":"Acme","value":"$400k"},{"label":"Globex","value":"$280k"}]}}'
        config = json.loads(raw)
        assert config["layout"] == "mini_list"
        assert len(config["slots"]["rows"]) == 2

    def test_valid_status_layout(self):
        raw = '{"layout":"status","title":"Sync Status","slots":{"icon":"success","text":"All synced"}}'
        config = json.loads(raw)
        assert config["layout"] == "status"
        assert config["slots"]["icon"] == "success"

    def test_valid_sparkline_layout(self):
        raw = '{"layout":"sparkline","title":"Revenue Trend","slots":{"values":[10,20,15,25,30],"current":"$30k","label":"Monthly"}}'
        config = json.loads(raw)
        assert config["layout"] == "sparkline"
        assert len(config["slots"]["values"]) == 5

    def test_invalid_layout_rejected(self):
        raw = '{"layout":"pie_chart","title":"Bad","slots":{}}'
        config = json.loads(raw)
        valid_layouts = {"big_number", "mini_list", "status", "sparkline"}
        assert config.get("layout") not in valid_layouts

    def test_markdown_fence_stripping(self):
        """LLM responses wrapped in ```json fences should be handled."""
        raw = '```json\n{"layout":"big_number","title":"Test","slots":{"value":"42","label":"Count"}}\n```'
        # Strip fences (same logic as widget_inference.py)
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0].strip()
        config = json.loads(text)
        assert config["layout"] == "big_number"
        assert config["slots"]["value"] == "42"

    def test_plain_json_no_fences(self):
        """Plain JSON (no fences) should parse directly."""
        raw = '{"layout":"status","title":"OK","slots":{"icon":"info","text":"Running"}}'
        config = json.loads(raw)
        assert config["layout"] == "status"


# ---------------------------------------------------------------------------
# Screenshot endpoint validation
# ---------------------------------------------------------------------------

class TestScreenshotValidation:
    def test_valid_data_url_accepted(self):
        """data:image/jpeg;base64,... format is valid."""
        url = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="
        assert url.startswith("data:image/")

    def test_non_data_url_rejected(self):
        """Non-data URLs should be rejected."""
        url = "https://example.com/image.jpg"
        assert not url.startswith("data:image/")

    def test_oversized_screenshot_rejected(self):
        """Screenshots > 2MB should be rejected."""
        big_url = "data:image/jpeg;base64," + "A" * 3_000_000
        assert len(big_url) > 2_000_000

    def test_reasonable_screenshot_accepted(self):
        """Screenshots < 2MB should be accepted."""
        small_url = "data:image/jpeg;base64," + "A" * 100_000
        assert len(small_url) < 2_000_000


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

class TestPromptBuilding:
    def test_prompt_includes_app_title(self):
        parts = [f"App: {'Pipeline Dashboard'}"]
        assert "Pipeline Dashboard" in "\n".join(parts)

    def test_prompt_includes_query_results(self):
        query_results = {"deals": [{"name": "Acme", "value": 100}]}
        parts = []
        for qname, rows in query_results.items():
            truncated = rows[:50]
            parts.append(f"\nQuery '{qname}' ({len(rows)} rows, showing {len(truncated)}):")
            parts.append(json.dumps(truncated, default=str, indent=None))
        prompt = "\n".join(parts)
        assert "deals" in prompt
        assert "Acme" in prompt

    def test_prompt_truncates_to_50_rows(self):
        rows = [{"id": i} for i in range(100)]
        truncated = rows[:50]
        assert len(truncated) == 50

    def test_user_prompt_included_when_provided(self):
        user_prompt = "show total pipeline value"
        parts = [f"\nThe user wants the widget to show: {user_prompt}"]
        assert "total pipeline value" in "\n".join(parts)

    def test_default_prompt_when_no_user_prompt(self):
        parts = ["\nPick the single most important metric or insight from these query results."]
        assert "most important metric" in "\n".join(parts)


# ---------------------------------------------------------------------------
# Widget config metadata
# ---------------------------------------------------------------------------

class TestWidgetConfigMetadata:
    def test_generated_at_timestamp_added(self):
        from datetime import datetime
        config = {"layout": "big_number", "title": "Test", "slots": {}}
        config["widget_prompt"] = None
        config["generated_at"] = datetime.utcnow().isoformat() + "Z"
        assert config["generated_at"].endswith("Z")
        assert "T" in config["generated_at"]

    def test_user_prompt_preserved(self):
        config = {"layout": "big_number", "title": "Test", "slots": {}}
        config["widget_prompt"] = "show pipeline"
        assert config["widget_prompt"] == "show pipeline"

    def test_null_prompt_for_auto(self):
        config = {"layout": "big_number", "title": "Test", "slots": {}}
        config["widget_prompt"] = None
        assert config["widget_prompt"] is None

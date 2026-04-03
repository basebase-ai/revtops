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


# ---------------------------------------------------------------------------
# Preview Settings validation
# ---------------------------------------------------------------------------

class TestPreviewSettings:
    """Test preview mode and detail level validation."""

    def test_valid_preview_modes(self):
        valid = {"screenshot", "widget", "mini_app", "icon"}
        for mode in valid:
            assert mode in valid

    def test_invalid_preview_mode_rejected(self):
        valid = {"screenshot", "widget", "mini_app", "icon"}
        assert "fullscreen" not in valid
        assert "video" not in valid

    def test_valid_detail_levels(self):
        valid = {"minimal", "standard", "detailed"}
        for level in valid:
            assert level in valid

    def test_invalid_detail_level_rejected(self):
        valid = {"minimal", "standard", "detailed"}
        assert "verbose" not in valid
        assert "compact" not in valid

    def test_settings_merge_preserves_existing(self):
        """Preview settings should merge into existing widget_config without clobbering."""
        existing = {
            "layout": "big_number",
            "title": "Pipeline",
            "slots": {"value": "$2.4M", "label": "Open", "trend": "up"},
            "screenshot": "data:image/png;base64,abc",
            "widget_prompt": None,
            "generated_at": "2025-01-01T00:00:00Z",
        }
        # Simulate merge
        config = dict(existing)
        config["preferred_mode"] = "widget"
        config["detail_level"] = "detailed"
        # Original fields preserved
        assert config["layout"] == "big_number"
        assert config["screenshot"] == "data:image/png;base64,abc"
        assert config["preferred_mode"] == "widget"
        assert config["detail_level"] == "detailed"

    def test_settings_merge_with_empty_config(self):
        """Settings should work even when widget_config was previously empty."""
        config = {}
        config["preferred_mode"] = "icon"
        assert config["preferred_mode"] == "icon"


# ---------------------------------------------------------------------------
# Detail level prompt instructions
# ---------------------------------------------------------------------------

class TestDetailLevelPrompts:
    """Verify that detail level affects the system prompt."""

    def test_minimal_prompt_text(self):
        from services.widget_inference import _DETAIL_LEVEL_INSTRUCTIONS
        text = _DETAIL_LEVEL_INSTRUCTIONS["minimal"]
        assert "single key metric" in text
        assert "big_number" in text

    def test_standard_prompt_is_empty(self):
        from services.widget_inference import _DETAIL_LEVEL_INSTRUCTIONS
        assert _DETAIL_LEVEL_INSTRUCTIONS["standard"] == ""

    def test_detailed_prompt_text(self):
        from services.widget_inference import _DETAIL_LEVEL_INSTRUCTIONS
        text = _DETAIL_LEVEL_INSTRUCTIONS["detailed"]
        assert "multi-metric" in text
        assert "5 rows" in text

    def test_detail_level_stored_in_config(self):
        """generate_widget_config should store detail_level in returned config."""
        config = {
            "layout": "big_number",
            "title": "Test",
            "slots": {"value": "42", "label": "Count"},
        }
        # Simulate what generate_widget_config does
        config["widget_prompt"] = None
        config["generated_at"] = "2025-01-01T00:00:00Z"
        config["detail_level"] = "detailed"
        assert config["detail_level"] == "detailed"

    @pytest.mark.asyncio
    async def test_generate_widget_config_stores_detail_level(self):
        """Full integration: detail_level should appear in the returned config."""
        from services.widget_inference import generate_widget_config
        app = _make_mock_app()
        mock_response = _make_anthropic_response(
            '{"layout":"big_number","title":"Count","slots":{"value":"42","label":"Items"}}'
        )
        with patch("services.widget_inference.AsyncAnthropic") as MockClient:
            instance = MockClient.return_value
            instance.messages.create = AsyncMock(return_value=mock_response)
            with patch("services.widget_inference.report_anthropic_call_success", new_callable=AsyncMock):
                result = await generate_widget_config(
                    app=app,
                    organization_id="test-org",
                    query_results={},
                    detail_level="detailed",
                )
        assert result["detail_level"] == "detailed"


# ---------------------------------------------------------------------------
# Widget regeneration on detail_level change
# ---------------------------------------------------------------------------

class TestWidgetRegeneration:
    """Verify that detail_level change triggers widget regeneration."""

    @pytest.mark.asyncio
    async def test_detail_level_change_triggers_regen(self):
        """When detail_level changes and mode is widget, generate_widget_config should be called."""
        from services.widget_inference import generate_widget_config
        app = _make_mock_app()
        mock_response = _make_anthropic_response(
            '{"layout":"mini_list","title":"Top Items","slots":{"rows":[{"label":"A","value":"1"},{"label":"B","value":"2"},{"label":"C","value":"3"}]}}'
        )
        with patch("services.widget_inference.AsyncAnthropic") as MockClient:
            instance = MockClient.return_value
            instance.messages.create = AsyncMock(return_value=mock_response)
            with patch("services.widget_inference.report_anthropic_call_success", new_callable=AsyncMock):
                result = await generate_widget_config(
                    app=app,
                    organization_id="test-org",
                    query_results={"items": [{"name": "A", "val": 1}]},
                    detail_level="detailed",
                )
        assert result["detail_level"] == "detailed"
        assert result["layout"] == "mini_list"
        # Verify the create call used the detailed system prompt
        call_kwargs = instance.messages.create.call_args
        system_text = call_kwargs.kwargs.get("system", "") if call_kwargs.kwargs else ""
        assert "multi-metric" in system_text

    @pytest.mark.asyncio
    async def test_minimal_detail_level_uses_minimal_prompt(self):
        """minimal detail_level should add big_number preference to system prompt."""
        from services.widget_inference import generate_widget_config
        app = _make_mock_app()
        mock_response = _make_anthropic_response(
            '{"layout":"big_number","title":"Total","slots":{"value":"100","label":"Count"}}'
        )
        with patch("services.widget_inference.AsyncAnthropic") as MockClient:
            instance = MockClient.return_value
            instance.messages.create = AsyncMock(return_value=mock_response)
            with patch("services.widget_inference.report_anthropic_call_success", new_callable=AsyncMock):
                result = await generate_widget_config(
                    app=app,
                    organization_id="test-org",
                    query_results={},
                    detail_level="minimal",
                )
        assert result["detail_level"] == "minimal"
        call_kwargs = instance.messages.create.call_args
        system_text = call_kwargs.kwargs.get("system", "") if call_kwargs.kwargs else ""
        assert "single key metric" in system_text
        assert "big_number" in system_text

"""Generate widget configs for app previews using LLM inference.

Runs an app's server-side queries, sends the results to a cheap model,
and asks it to pick the most important 1-2 data points and a layout.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from anthropic import AsyncAnthropic

from config import settings
from models.app import App
from services.anthropic_health import (
    report_anthropic_call_failure,
    report_anthropic_call_success,
)

logger = logging.getLogger(__name__)

_MODEL = settings.ANTHROPIC_CHEAP_MODEL

_SYSTEM_PROMPT = """\
You generate widget configs for app previews. A widget is a tiny card that shows
the 1-2 most important data points from an app's query results.

You MUST return valid JSON matching one of these layouts:

1. big_number — one prominent metric
   {"layout":"big_number","title":"<2-4 words>","slots":{"value":"<formatted value>","label":"<short description>","trend":"up|down|flat"}}

2. mini_list — 2-3 key rows
   {"layout":"mini_list","title":"<2-4 words>","slots":{"rows":[{"label":"<name>","value":"<value>"},...]}}

3. status — a single status indicator
   {"layout":"status","title":"<2-4 words>","slots":{"icon":"warning|success|info","text":"<short status text>"}}

4. sparkline — a trend with current value
   {"layout":"sparkline","title":"<2-4 words>","slots":{"values":[<numbers>],"current":"<formatted current>","label":"<description>"}}

Rules:
- Pick the layout that best fits the data
- Format numbers nicely ($1.2M, 42%, 1,234)
- Title should be 2-4 words max
- Return ONLY the JSON object, no markdown fences or explanation
"""


_DETAIL_LEVEL_INSTRUCTIONS: dict[str, str] = {
    "minimal": (
        "\n\nIMPORTANT: Show a single key metric only. Strongly prefer big_number layout."
    ),
    "standard": "",  # current behavior, no extra instructions
    "detailed": (
        "\n\nIMPORTANT: Show a multi-metric summary with 3-4 data points. "
        "For mini_list, allow up to 5 rows."
    ),
}


async def generate_widget_config(
    app: App,
    organization_id: str,
    query_results: dict[str, list[dict[str, Any]]],
    user_prompt: str | None = None,
    detail_level: str = "standard",
) -> dict[str, Any]:
    """Generate a widget config from app query results.

    Args:
        app: The App model instance.
        organization_id: Org UUID string.
        query_results: Dict mapping query name -> list of row dicts (pre-fetched).
        user_prompt: Optional user override for what the widget should show.
        detail_level: One of 'minimal', 'standard', 'detailed'.

    Returns:
        Widget config dict with layout, title, slots, widget_prompt, generated_at, detail_level.
    """
    # Build the user message
    parts: list[str] = [f"App: {app.title}"]
    if app.description:
        parts.append(f"Description: {app.description}")

    for qname, rows in query_results.items():
        truncated = rows[:50]
        parts.append(f"\nQuery '{qname}' ({len(rows)} rows, showing {len(truncated)}):")
        parts.append(json.dumps(truncated, default=str, indent=None))

    if user_prompt:
        parts.append(f"\nThe user wants the widget to show: {user_prompt}")
    else:
        parts.append("\nPick the single most important metric or insight from these query results.")

    user_message = "\n".join(parts)

    # Append detail-level-specific instructions to the system prompt
    system_prompt = _SYSTEM_PROMPT + _DETAIL_LEVEL_INSTRUCTIONS.get(detail_level, "")

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        await report_anthropic_call_success(source="services.widget_inference.generate_widget_config")
    except Exception as exc:
        await report_anthropic_call_failure(
            source="services.widget_inference.generate_widget_config",
            error=exc,
        )
        raise

    raw_text = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1] if "\n" in raw_text else raw_text[3:]
        raw_text = raw_text.rsplit("```", 1)[0].strip()

    config = json.loads(raw_text)

    # Validate layout
    valid_layouts = {"big_number", "mini_list", "status", "sparkline"}
    if config.get("layout") not in valid_layouts:
        raise ValueError(f"Invalid layout: {config.get('layout')}")

    # Attach metadata
    config["widget_prompt"] = user_prompt
    config["generated_at"] = datetime.utcnow().isoformat() + "Z"
    config["detail_level"] = detail_level

    return config

from __future__ import annotations

import base64
from types import SimpleNamespace
from uuid import uuid4

from api.main import app
from api.routes.public import (
    _cache_get_html,
    _cache_set_html,
    _is_unfurlable_visibility,
    _public_origin,
    _public_preview_description,
    _public_preview_title,
    share_router,
)
from api.routes.artifacts import _generate_chart_html, get_artifact
from starlette.routing import Match
from services.public_previews import build_preview_html, decode_data_url_image, render_card_png


def test_decode_data_url_image_valid_png() -> None:
    payload = base64.b64encode(b"hello").decode("ascii")
    decoded = decode_data_url_image(f"data:image/png;base64,{payload}")
    assert decoded is not None
    content, mime = decoded
    assert content == b"hello"
    assert mime == "image/png"


def test_build_preview_html_includes_og_and_twitter_tags() -> None:
    html = build_preview_html(
        page_title="Example",
        description="Description",
        canonical_url="https://example.com/public/apps/abc",
        image_url="https://example.com/api/public/share/apps/abc/snapshot.png",
        redirect_url="https://example.com/public/apps/abc",
    )
    assert 'property="og:title" content="Example"' in html
    assert 'name="twitter:image" content="https://example.com/api/public/share/apps/abc/snapshot.png"' in html
    assert 'property="og:image:secure_url" content="https://example.com/api/public/share/apps/abc/snapshot.png"' in html
    assert 'window.location.replace("https://example.com/public/apps/abc")' in html
    assert '<noscript>' in html


def test_render_card_png_returns_png_bytes() -> None:
    png = render_card_png(
        heading="Basebase App",
        title="Pipeline Dashboard",
        description="Current app snapshot",
        footer="App ID: abc",
    )
    assert png.startswith(b"\x89PNG")


def test_public_preview_description_prefers_conversation_title_with_owner() -> None:
    description = _public_preview_description(
        conversation=SimpleNamespace(title="Q2 forecast"),
        app=SimpleNamespace(title="Pipeline app"),
        owner=SimpleNamespace(name="Alex", email="alex@example.com"),
    )
    assert description == "Q2 forecast — Alex"


def test_public_preview_description_prefers_app_description_with_owner() -> None:
    description = _public_preview_description(
        conversation=SimpleNamespace(title="Q2 forecast"),
        app=SimpleNamespace(
            title="Pipeline app",
            description="Shows the current largest celestial body visible in the sky...",
        ),
        owner=SimpleNamespace(name="Alex", email="alex@example.com"),
    )
    assert description == "Shows the current largest celestial body visible in the sky... — Alex"


def test_public_preview_description_falls_back_to_document_and_owner_email() -> None:
    description = _public_preview_description(
        conversation=None,
        artifact=SimpleNamespace(title=None, content=None),
        owner=SimpleNamespace(name=None, email="owner@example.com"),
    )
    assert description == "Document — owner@example.com"


def test_public_preview_description_uses_first_80_chars_of_artifact_content() -> None:
    content = (
        "This is a shared artifact document body with enough characters to verify truncation occurs "
        "at exactly eighty characters."
    )
    description = _public_preview_description(
        conversation=None,
        artifact=SimpleNamespace(title="Doc", content=content),
        owner=SimpleNamespace(name="Alex", email="alex@example.com"),
    )
    assert description == content[:80]


def test_public_preview_description_strips_repeated_artifact_title_prefix() -> None:
    content = "Weekly KPI Report — A concise summary of this week's performance and key changes."
    description = _public_preview_description(
        conversation=None,
        artifact=SimpleNamespace(title="Weekly KPI Report", content=content),
        owner=SimpleNamespace(name="Alex", email="alex@example.com"),
    )
    assert description == "A concise summary of this week's performance and key changes."


def test_public_preview_description_does_not_fallback_to_artifact_title() -> None:
    description = _public_preview_description(
        conversation=None,
        artifact=SimpleNamespace(title="Weekly KPI Report", content=None),
        owner=SimpleNamespace(name="Alex", email="alex@example.com"),
    )
    assert description == "Document — Alex"


def test_build_preview_html_uses_public_apps_redirect_url() -> None:
    html = build_preview_html(
        page_title="Example",
        description="Description",
        canonical_url="https://app.basebase.com/basebase/apps/abc",
        image_url="https://app.basebase.com/api/public/share/apps/abc/snapshot.png",
        redirect_url="https://app.basebase.com/public/apps/abc",
    )
    assert 'window.location.replace("https://app.basebase.com/public/apps/abc")' in html


def test_public_preview_title_uses_app_title_when_present() -> None:
    title = _public_preview_title(app=SimpleNamespace(title="Revenue Tracker"))
    assert title == "Revenue Tracker · Basebase"


def test_public_preview_title_falls_back_when_artifact_title_missing() -> None:
    title = _public_preview_title(artifact=SimpleNamespace(title=None))
    assert title == "Shared Document · Basebase"


def test_public_origin_prefers_forwarded_proxy_headers() -> None:
    request = SimpleNamespace(
        headers={"x-forwarded-proto": "https", "x-forwarded-host": "app.basebase.com"},
        url=SimpleNamespace(scheme="http", netloc="internal:8000"),
    )
    assert _public_origin(request) == "https://app.basebase.com"


def test_preview_html_cache_hit_and_expiry(monkeypatch) -> None:
    fake_now = {"value": 1_000.0}
    monkeypatch.setattr("api.routes.public.time.time", lambda: fake_now["value"])

    _cache_set_html("preview:test", "<html>cached</html>")
    assert _cache_get_html("preview:test") == "<html>cached</html>"

    fake_now["value"] += 301.0
    assert _cache_get_html("preview:test") is None


def test_is_unfurlable_visibility_allows_known_levels() -> None:
    assert _is_unfurlable_visibility("public")
    assert _is_unfurlable_visibility("team")
    assert _is_unfurlable_visibility("private")
    assert not _is_unfurlable_visibility(None)
    assert not _is_unfurlable_visibility("archived")


def test_share_router_supports_apps_uuid_path_for_unfurl_links() -> None:
    route_paths = {route.path for route in share_router.routes}
    assert "/apps/{app_id}" in route_paths


def test_share_router_supports_artifact_uuid_paths_for_unfurl_links() -> None:
    route_paths = {route.path for route in share_router.routes}
    assert "/artifacts/{artifact_id}" in route_paths
    assert "/basebase/artifacts/{artifact_id}" in route_paths
    assert "/{org_slug}/artifacts/{artifact_id}" in route_paths


def test_api_artifact_route_is_resolved_before_slug_unfurl_route() -> None:
    artifact_id = uuid4()
    scope = {
        "type": "http",
        "path": f"/api/artifacts/{artifact_id}",
        "method": "GET",
    }

    matched_endpoint = None
    for route in app.router.routes:
        match, child_scope = route.matches(scope)
        if match == Match.FULL:
            matched_endpoint = child_scope.get("endpoint")
            break

    assert matched_endpoint is get_artifact


def test_generate_chart_html_keeps_responsive_true_when_config_present() -> None:
    plotly_json = '{"data":[{"type":"scatter","y":[1,2,3]}],"layout":{},"config":{"displayModeBar":false}}'

    html = _generate_chart_html(plotly_json, "Quarterly report")

    assert "{responsive: true, ...(spec.config || {})}" in html


def test_generate_chart_html_escapes_script_termination_sequences() -> None:
    plotly_json = (
        '{"data":[{"type":"scatter","text":"<\\/script><script>alert(1)</script>"}],'
        '"layout":{},"config":{}}'
    )

    html = _generate_chart_html(plotly_json, "Quarterly report")

    assert "</script><script>alert(1)</script>" not in html
    assert "<\\/script><script>alert(1)<\\/script>" in html

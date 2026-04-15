from __future__ import annotations

import base64
from types import SimpleNamespace

from api.routes.public import _public_origin, _public_preview_description, _public_preview_title
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
    assert 'http-equiv="refresh"' in html


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


def test_public_preview_description_falls_back_to_document_and_owner_email() -> None:
    description = _public_preview_description(
        conversation=None,
        artifact=SimpleNamespace(title=None),
        owner=SimpleNamespace(name=None, email="owner@example.com"),
    )
    assert description == "Document — owner@example.com"


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

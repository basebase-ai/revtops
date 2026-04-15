from __future__ import annotations

import base64

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
    assert 'http-equiv="refresh"' in html


def test_render_card_png_returns_png_bytes() -> None:
    png = render_card_png(
        heading="Basebase App",
        title="Pipeline Dashboard",
        description="Current app snapshot",
        footer="App ID: abc",
    )
    assert png.startswith(b"\x89PNG")

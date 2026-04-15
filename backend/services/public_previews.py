from __future__ import annotations

import base64
import binascii
from html import escape
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont


def decode_data_url_image(data_url: str | None) -> tuple[bytes, str] | None:
    if not data_url or not data_url.startswith("data:image/"):
        return None
    marker = ";base64,"
    if marker not in data_url:
        return None
    mime_type = data_url[5:data_url.index(marker)]
    encoded = data_url[data_url.index(marker) + len(marker):]
    try:
        return base64.b64decode(encoded, validate=True), mime_type
    except (ValueError, binascii.Error):
        return None


def render_card_png(
    *,
    heading: str,
    title: str,
    description: str,
    footer: str,
    width: int = 1200,
    height: int = 630,
) -> bytes:
    image = Image.new("RGB", (width, height), color=(16, 18, 25))
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default()
    subtitle_font = ImageFont.load_default()
    body_font = ImageFont.load_default()

    draw.rectangle((0, 0, width, 8), fill=(99, 102, 241))
    draw.text((56, 48), heading, fill=(165, 180, 252), font=subtitle_font)
    draw.text((56, 100), title[:120], fill=(255, 255, 255), font=title_font)
    draw.text((56, 190), description[:260], fill=(209, 213, 219), font=body_font)
    draw.text((56, height - 72), footer[:140], fill=(156, 163, 175), font=subtitle_font)

    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def build_preview_html(
    *,
    page_title: str,
    description: str,
    canonical_url: str,
    image_url: str,
    redirect_url: str,
) -> str:
    safe_title = escape(page_title)
    safe_desc = escape(description)
    safe_canonical = escape(canonical_url)
    safe_image = escape(image_url)
    safe_redirect = escape(redirect_url)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{safe_title}</title>
    <meta name="description" content="{safe_desc}" />
    <link rel="canonical" href="{safe_canonical}" />
    <meta property="og:type" content="website" />
    <meta property="og:site_name" content="Basebase" />
    <meta property="og:title" content="{safe_title}" />
    <meta property="og:description" content="{safe_desc}" />
    <meta property="og:url" content="{safe_canonical}" />
    <meta property="og:image" content="{safe_image}" />
    <meta property="og:image:secure_url" content="{safe_image}" />
    <meta property="og:image:alt" content="{safe_title}" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="{safe_title}" />
    <meta name="twitter:description" content="{safe_desc}" />
    <meta name="twitter:image" content="{safe_image}" />
    <meta http-equiv="refresh" content="0;url={safe_redirect}" />
  </head>
  <body>
    <script>window.location.replace("{safe_redirect}");</script>
  </body>
</html>"""

"""Extract favicon URL from website HTML for use as organization logo."""
from __future__ import annotations

import logging
import re
from typing import Optional
from uuid import UUID
from urllib.parse import urljoin, urlparse

import httpx

from models.database import get_admin_session
from models.organization import Organization

logger = logging.getLogger(__name__)

# Link tag regex: captures rel, href, type, sizes
_LINK_RE = re.compile(
    r'<link\s+([^>]*?)>',
    re.IGNORECASE | re.DOTALL,
)

_ATTR_RE = re.compile(
    r'\b(rel|href|type|sizes)\s*=\s*["\']([^"\']*)["\']',
    re.IGNORECASE,
)


def _parse_link_tag(match: re.Match[str]) -> Optional[dict[str, str]]:
    """Parse a single link tag, return dict with rel, href, type, sizes or None."""
    attrs: dict[str, str] = {}
    for m in _ATTR_RE.finditer(match.group(1)):
        attrs[m.group(1).lower()] = m.group(2).strip()
    href: Optional[str] = attrs.get("href")
    rel: Optional[str] = attrs.get("rel")
    if not href or not rel:
        return None
    rel_lower: str = rel.lower()
    if "icon" not in rel_lower and "shortcut" not in rel_lower:
        return None
    return {
        "href": href,
        "rel": rel_lower,
        "type": attrs.get("type", "").lower(),
        "sizes": attrs.get("sizes", "").lower(),
    }


def _score_candidate(c: dict[str, str]) -> int:
    """Higher score = better. Prefer SVG, then apple-touch-icon, then large sizes."""
    score: int = 0
    if "image/svg+xml" in c.get("type", ""):
        score += 100  # SVG is scalable, best for logos
    if "apple-touch-icon" in c.get("rel", ""):
        score += 80  # Usually 180x180 or larger
    sizes: str = c.get("sizes", "")
    if sizes:
        if sizes == "any":
            score += 50
        else:
            # Parse "192x192" or "192x192 512x512"
            for part in sizes.replace("x", " ").split():
                try:
                    n: int = int(part)
                    if n >= 192:
                        score += 60
                        break
                    if n >= 64:
                        score += 30
                        break
                except ValueError:
                    pass
    return score


def extract_favicon_from_html(html: str, base_url: str) -> Optional[str]:
    """Extract the best favicon URL from HTML <head> link tags.

    Prefers: SVG > apple-touch-icon > large sizes > default icon.
    Resolves relative hrefs to absolute URLs.
    """
    # Limit to <head> to avoid body content
    head_match: Optional[re.Match[str]] = re.search(
        r"<head[^>]*>(.*?)</head>",
        html,
        re.IGNORECASE | re.DOTALL,
    )
    search_region: str = head_match.group(1) if head_match else html

    candidates: list[tuple[int, str]] = []
    for m in _LINK_RE.finditer(search_region):
        parsed: Optional[dict[str, str]] = _parse_link_tag(m)
        if not parsed:
            continue
        try:
            absolute: str = urljoin(base_url, parsed["href"])
            if not absolute.startswith(("http://", "https://")):
                continue
            parsed_url = urlparse(absolute)
            if not parsed_url.netloc:
                continue
            score: int = _score_candidate(parsed)
            candidates.append((score, absolute))
        except Exception:
            continue

    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][1]


def _normalize_url_for_fetch(raw: str) -> Optional[str]:
    """Ensure URL has a scheme so httpx can fetch it. Returns None if invalid."""
    s: str = (raw or "").strip()
    if not s:
        return None
    if s.startswith(("http://", "https://")):
        return s
    # Common input: "nango.dev" or "www.nango.dev" — assume HTTPS
    return f"https://{s}"


async def fetch_favicon_from_url(page_url: str) -> Optional[str]:
    """Fetch a webpage and extract its favicon URL from the HTML head.

    Returns the absolute favicon URL, or None if not found or on error.
    Falls back to /favicon.ico if no link tags found.
    Accepts URLs with or without scheme (e.g. "nango.dev" → "https://nango.dev").
    """
    url: Optional[str] = _normalize_url_for_fetch(page_url)
    if not url:
        logger.info("[favicon] Skipping empty or invalid URL: %r", page_url[:100] if page_url else "")
        return None
    if url != (page_url or "").strip():
        logger.info("[favicon] Normalized URL %r -> %s", page_url[:80], url)
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; Basebase/1.0)",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            base: str = str(response.url)
            if response.status_code >= 400:
                logger.info("[favicon] HTTP %s for %s (final URL %s)", response.status_code, url, base)
                return None
            logger.info("[favicon] Fetched %s -> %s (%d bytes)", url, base, len(response.text))
            html: str = response.text
    except Exception as e:
        logger.warning("[favicon] Failed to fetch %s: %s", url, e)
        return None

    favicon: Optional[str] = extract_favicon_from_html(html, base)
    if favicon:
        logger.info("[favicon] Extracted favicon from link tag: %s", favicon[:100])
        return favicon
    fallback: str = urljoin(base, "/favicon.ico")
    logger.info(
        "[favicon] No icon link tags in HTML (%d bytes), using fallback: %s",
        len(html),
        fallback,
    )
    return fallback


async def update_org_logo_from_website(org_id: UUID, website_url: str) -> None:
    """Background task: fetch favicon from website and update organization.logo_url."""
    logger.info("[favicon] Starting favicon fetch for org %s from %s", org_id, website_url)
    favicon_url: Optional[str] = await fetch_favicon_from_url(website_url)
    if not favicon_url:
        logger.info("[favicon] No favicon found for org %s from %s", org_id, website_url)
        return
    # Truncate to column limit (512)
    if len(favicon_url) > 512:
        logger.info("[favicon] Truncating favicon URL from %d to 512 chars for org %s", len(favicon_url), org_id)
        favicon_url = favicon_url[:512]
    try:
        async with get_admin_session() as session:
            org: Organization | None = await session.get(Organization, org_id)
            if not org:
                logger.warning("[favicon] Org %s not found, cannot update logo", org_id)
                return
            org.logo_url = favicon_url
            await session.commit()
            logger.info("[favicon] Updated org %s logo from %s -> %s", org_id, website_url, favicon_url[:80])
    except Exception as e:
        logger.warning("[favicon] Failed to update org %s logo: %s", org_id, e)

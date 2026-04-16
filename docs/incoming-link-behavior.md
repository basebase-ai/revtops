# Incoming link behavior: public vs. private/team resources and scrapers vs. users

This document describes how incoming links should behave for **Apps** and **Documents (Artifacts)**, with explicit separation between:

1. **Bots/scrapers** (Slack, Discord, X/Twitter, LinkedIn, etc.) that need Open Graph metadata.
2. **Humans** actually clicking/tapping links and expecting the interactive page.

## Current architecture in this repo

- Resources support `private | team | public` visibility.
- Public-facing interactive views live under:
  - `/public/apps/:id`
  - `/public/artifacts/:id`
- Metadata/unfurl endpoints live under:
  - `/api/public/share/apps/:id`
  - `/api/public/share/artifacts/:id`
  - each with `/snapshot.png` image endpoints
- Frontend nginx routes known bot user-agents to metadata endpoints, while normal users continue into the SPA route.

## Recommended behavior contract

### 1) Human click/tap flow (browser navigation)

- For canonical share links like:
  - `/basebase/apps/:id`
  - `/basebase/documents/:id`
- A real browser should end at the interactive page:
  - `/public/apps/:id` for apps
  - `/public/artifacts/:id` for documents
- If visibility is not public:
  - show a clear "not public / access denied" experience,
  - never leak actual content to unauthenticated users.

### 2) Bot/scraper flow (unfurl fetch)

- Bots should receive HTML that contains:
  - `og:title`, `og:description`, `og:url`, `og:image`
  - Twitter equivalents (`twitter:title`, `twitter:description`, `twitter:image`)
- The metadata HTML may include JS redirect to interactive page for humans, but metadata must be available in initial HTML response.
- `og:image` should point at deterministic snapshot URL and be cacheable for short TTL.

### 3) Visibility rules for unfurl content

For each visibility level:

- **public**:
  - full unfurl allowed (title/description/image),
  - human click leads to public interactive view.
- **team/private**:
  - never include sensitive body content in OG metadata,
  - if preview is allowed, keep it minimal (generic title/description and optionally generic image),
  - human click should route to authenticated app and enforce authz.

### 4) Canonical URLs

- `og:url` and `<link rel="canonical">` should use stable public-facing routes.
- Avoid canonical URLs that vary by internal host/proxy details.
- Keep share URL shape stable over time; support legacy forms with redirects/rewrite only.

### 5) Caching and invalidation

- Metadata and snapshot responses should be cacheable with short TTL (e.g., 5 minutes).
- Cache key should include content/version markers (`updated_at`, title/description hash, etc.) so updates bust cache naturally.
- Warm metadata/image endpoints when a link is created/copied to reduce stale first-unfurl results.

### 6) Security posture

- Treat all `/api/public/share/*` endpoints as internet-facing.
- Do not expose raw private/team content in OG metadata or snapshot text.
- Sanitize all metadata fields before HTML interpolation.
- Prefer explicit allow-listing of unfurlable visibilities and keep logging for non-public access attempts.

## Practical decision matrix

| Actor | Resource visibility | Expected response |
|---|---|---|
| Bot scraper | Public | 200 metadata HTML + OG image URL |
| Bot scraper | Team/Private | 200 with minimal/safe metadata (or 404 if strict) |
| Human click | Public | Redirect/render interactive public page |
| Human click | Team/Private | Auth flow + authorization check; no leaked content |

## Product notes

- If your intent is strict privacy semantics, return `404` for non-public resources even to scrapers.
- If your intent is "link exists but locked", return generic unfurl metadata without user content.
- Pick one model and apply consistently across apps and documents to avoid surprising behavior.

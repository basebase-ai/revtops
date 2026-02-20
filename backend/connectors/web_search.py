"""
Web Search connector – web search and URL fetching.

Same pattern as google_drive.py: one connector module (web_search.py) that
wraps Perplexity/Exa search and direct/ScrapingBee URL fetching so
organizations can enable or disable web access for the agent.
"""

import json
import logging
import re
from typing import Any

import httpx
from openai import AsyncOpenAI

from config import settings
from connectors.base import BaseConnector
from connectors.registry import (
    AuthType,
    Capability,
    ConnectorAction,
    ConnectorMeta,
    ConnectorScope,
)

logger = logging.getLogger(__name__)

_configured_openai_research_model: str = (settings.OPENAI_RESEARCH_MODEL or "").strip()
_preferred_openai_research_model: str = (
    _configured_openai_research_model
    if _configured_openai_research_model.startswith("gpt-5")
    else "gpt-5"
)
OPENAI_WEB_RESEARCH_FALLBACK_MODELS: tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            _preferred_openai_research_model,
            "gpt-5",
            "gpt-5-mini",
            "gpt-5-nano",
        )
    )
)


class WebSearchConnector(BaseConnector):
    """Web search and URL fetch – same implementation pattern as other connectors."""

    source_system: str = "web_search"
    meta = ConnectorMeta(
        name="Web Search",
        slug="web_search",
        auth_type=AuthType.CUSTOM,
        scope=ConnectorScope.ORGANIZATION,
        capabilities=[Capability.QUERY, Capability.ACTION],
        query_description=(
            "Search the web for real-time information. Pass a search query string. "
            "Optionally prefix with 'provider:exa ' or 'provider:perplexity ' to choose a provider (default exa). "
            "Exa returns per-result excerpts; Perplexity returns a single synthesized answer."
        ),
        actions=[
            ConnectorAction(
                name="fetch_url",
                description="Fetch and extract text content from a web URL.",
                parameters=[
                    {"name": "url", "type": "string", "required": True, "description": "URL to fetch (http:// or https://)"},
                    {"name": "render_js", "type": "boolean", "required": False, "description": "Render JavaScript via headless browser (slower, uses ScrapingBee credits)"},
                    {"name": "premium_proxy", "type": "boolean", "required": False, "description": "Use residential proxy for sites that block datacenter IPs"},
                    {"name": "extract_text", "type": "boolean", "required": False, "description": "Return clean text instead of raw HTML (default true)"},
                    {"name": "wait_ms", "type": "integer", "required": False, "description": "Wait time in ms after page load (only with render_js)"},
                ],
            ),
        ],
        description="Web search (Perplexity / Exa) and URL fetching",
    )

    async def sync_deals(self) -> int:
        return 0

    async def sync_accounts(self) -> int:
        return 0

    async def sync_contacts(self) -> int:
        return 0

    async def sync_activities(self) -> int:
        return 0

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        return {}

    async def query(self, request: str) -> dict[str, Any]:
        provider: str = "exa"
        query: str = request.strip()
        if query.lower().startswith("provider:perplexity "):
            provider = "perplexity"
            query = query[len("provider:perplexity "):].strip()
        elif query.lower().startswith("provider:exa "):
            provider = "exa"
            query = query[len("provider:exa "):].strip()

        if not query:
            return {"error": "No search query provided"}

        if provider == "exa":
            return await self._search_exa(query)
        return await self._search_perplexity(query)

    async def execute_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if action == "fetch_url":
            return await self._fetch_url(params)
        raise ValueError(f"Unknown action: {action}")

    async def _search_perplexity(self, query: str) -> dict[str, Any]:
        if not settings.PERPLEXITY_API_KEY:
            fallback: dict[str, Any] | None = await self._openai_fallback(query, context_answer=None)
            if fallback:
                return {
                    "query": query,
                    "provider": "perplexity",
                    "answer": None,
                    "sources": [],
                    "results": [],
                    "openai_fallback": fallback,
                    "fallback_reason": "perplexity_not_configured",
                }
            return {"error": "Web search not configured (no PERPLEXITY_API_KEY or OPENAI_API_KEY)", "query": query}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response: httpx.Response = await client.post(
                    "https://api.perplexity.ai/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.PERPLEXITY_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "sonar",
                        "messages": [
                            {"role": "system", "content": "You are a helpful research assistant. Provide concise, factual answers with relevant details. Focus on information useful for sales and business contexts."},
                            {"role": "user", "content": f"Search query: {query}"},
                        ],
                    },
                )
                if response.status_code != 200:
                    logger.error("[WebSearch] Perplexity error: %s %s", response.status_code, response.text)
                    return {"error": f"Search API error: {response.status_code}", "query": query, "provider": "perplexity"}

                data: dict[str, Any] = response.json()
                content: str = data["choices"][0]["message"]["content"]
                citations: list[str] = data.get("citations", [])
                result: dict[str, Any] = {
                    "query": query,
                    "provider": "perplexity",
                    "answer": content,
                    "sources": citations,
                    "results": [],
                }
                fallback_result: dict[str, Any] | None = await self._openai_fallback(query, context_answer=content)
                if fallback_result:
                    result["openai_fallback"] = fallback_result
                    result["fallback_reason"] = "always_openai_supplement"
                return result
        except httpx.TimeoutException:
            return {"error": "Search request timed out", "query": query, "provider": "perplexity"}
        except Exception as e:
            logger.error("[WebSearch] Perplexity search failed: %s", e)
            return {"error": f"Search failed: {e}", "query": query, "provider": "perplexity"}

    async def _search_exa(self, query: str) -> dict[str, Any]:
        if not settings.EXA_API_KEY:
            return {"error": "Exa search is not configured (no EXA_API_KEY)", "query": query, "provider": "exa"}
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response: httpx.Response = await client.post(
                    "https://api.exa.ai/search",
                    headers={"x-api-key": settings.EXA_API_KEY, "Content-Type": "application/json"},
                    json={
                        "query": query,
                        "numResults": 10,
                        "type": "auto",
                        "contents": {"highlights": {"maxCharacters": 2000}},
                    },
                )
            if response.status_code != 200:
                logger.error("[WebSearch] Exa error: %s %s", response.status_code, response.text)
                return {"error": f"Exa API error: {response.status_code}", "query": query, "provider": "exa"}
            data: dict[str, Any] = response.json()
            exa_results: list[dict[str, Any]] = data.get("results", [])
            results: list[dict[str, Any]] = []
            sources: list[str] = []
            for r in exa_results:
                url: str = r.get("url", "")
                if url:
                    sources.append(url)
                highlights: list[str] = r.get("highlights") or []
                content_val: str | None = "\n\n".join(highlights).strip() or None if highlights else None
                if content_val is None and r.get("summary"):
                    content_val = r["summary"]
                results.append({"title": r.get("title") or "", "url": url, "content": content_val})
            return {"query": query, "provider": "exa", "answer": None, "sources": sources, "results": results}
        except httpx.TimeoutException:
            return {"error": "Exa search timed out", "query": query, "provider": "exa"}
        except Exception as e:
            logger.error("[WebSearch] Exa search failed: %s", e)
            return {"error": f"Search failed: {e}", "query": query, "provider": "exa"}

    async def _openai_fallback(self, query: str, context_answer: str | None) -> dict[str, Any] | None:
        if not settings.OPENAI_API_KEY:
            return None
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        prompt_context: str = context_answer or "No useful context was available."
        for model in OPENAI_WEB_RESEARCH_FALLBACK_MODELS:
            try:
                response = await client.chat.completions.create(
                    model=model,
                    temperature=0.2,
                    messages=[
                        {"role": "system", "content": "You are a research assistant for sales and GTM workflows. Given a search query and context, provide concise, factual synthesis useful for decision-making. If uncertain, clearly label uncertainty and suggest verification steps."},
                        {"role": "user", "content": f"Research query: {query}\n\nExisting sparse research context:\n{prompt_context}\n\nReturn concise findings with 4 sections: Key findings, Relevant context, Risks/unknowns, Suggested next actions."},
                    ],
                )
                content: str = (response.choices[0].message.content or "").strip()
                if content:
                    return {"answer": content, "provider": "openai", "model": model}
            except Exception as exc:
                logger.warning("[WebSearch] OpenAI fallback model %s failed: %s", model, exc)
                continue
        return None

    async def _fetch_url(self, params: dict[str, Any]) -> dict[str, Any]:
        url: str = (params.get("url") or "").strip()
        extract_text: bool = params.get("extract_text", True)
        render_js: bool = params.get("render_js", False)
        premium_proxy: bool = params.get("premium_proxy", False)
        wait_ms: int | None = params.get("wait_ms")

        if not url:
            return {"error": "No URL provided"}
        if not url.startswith(("http://", "https://")):
            return {"error": "URL must start with http:// or https://"}

        use_scrapingbee: bool = render_js or premium_proxy
        if use_scrapingbee and not settings.SCRAPINGBEE_API_KEY:
            return {"error": "ScrapingBee required for render_js/premium_proxy but SCRAPINGBEE_API_KEY is not set."}

        try:
            if use_scrapingbee:
                body: str = await self._fetch_via_scrapingbee(url, extract_text, render_js, premium_proxy, wait_ms)
            else:
                body = await self._fetch_direct(url)
        except httpx.TimeoutException:
            return {"error": "Request timed out", "url": url}
        except Exception as e:
            return {"error": f"Fetch failed: {e}", "url": url}

        if use_scrapingbee and extract_text:
            try:
                extracted: dict[str, Any] = json.loads(body)
                return self._truncate(url, extracted.get("text", body), mode="extracted_text")
            except (json.JSONDecodeError, KeyError):
                pass

        if extract_text and not use_scrapingbee:
            return self._truncate(url, self._strip_html(body), mode="extracted_text")

        return self._truncate(url, body, mode="html", max_chars=100_000)

    async def _fetch_direct(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response: httpx.Response = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; Revtops/1.0)",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            if response.status_code >= 400:
                raise Exception(f"HTTP {response.status_code} from {url}")
            return response.text

    async def _fetch_via_scrapingbee(
        self, url: str, extract_text: bool, render_js: bool, premium_proxy: bool, wait_ms: int | None
    ) -> str:
        sb_params: dict[str, str] = {"api_key": settings.SCRAPINGBEE_API_KEY, "url": url}  # type: ignore[arg-type]
        if extract_text:
            sb_params["extract_rules"] = json.dumps({"text": {"selector": "body", "type": "text"}})
        if render_js:
            sb_params["render_js"] = "true"
            if wait_ms is not None:
                sb_params["wait"] = str(max(0, min(wait_ms, 35000)))
        if premium_proxy:
            sb_params["premium_proxy"] = "true"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response: httpx.Response = await client.get("https://app.scrapingbee.com/api/v1/", params=sb_params)
            if response.status_code != 200:
                raise Exception(f"ScrapingBee returned status {response.status_code}: {response.text[:500]}")
            return response.text

    @staticmethod
    def _strip_html(html: str) -> str:
        text: str = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _truncate(url: str, content: str, *, mode: str, max_chars: int = 50_000) -> dict[str, Any]:
        truncated: bool = len(content) > max_chars
        if truncated:
            content = content[:max_chars]
        result: dict[str, Any] = {"url": url, "content": content, "mode": mode}
        if truncated:
            result["truncated"] = True
            result["note"] = f"Content truncated to {max_chars} characters"
        return result

"""Network tools: fetch_url and web_search."""

from __future__ import annotations

import asyncio
import html as html_module
import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import httpx

from .base import Preview, Tool, ToolContext, ToolResult, truncate_output

USER_AGENT = "OllamaCoder/0.3 (+https://github.com/lalomorales22/ollama-coder)"
# DuckDuckGo serves a challenge page to obvious bots; a normal browser UA
# is what keeps the free, key-less search path working.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Blocked so a tool call cannot be turned into an SSRF probe of the local network.
PRIVATE_HOSTS = re.compile(
    r"^(localhost|127\.|0\.0\.0\.0|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.|\[?::1\]?)",
    re.IGNORECASE,
)


def html_to_text(html: str) -> str:
    html = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<h([1-6])[^>]*>(.*?)</h\1>", lambda m: f"\n\n{'#' * int(m.group(1))} {m.group(2)}\n", html, flags=re.S | re.I)
    html = re.sub(r"<li[^>]*>(.*?)</li>", r"\n- \1", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</(p|div|section|article|tr|h[1-6])>", "\n\n", html, flags=re.I)
    html = re.sub(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", r"\2 (\1)", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = html_module.unescape(html)
    html = re.sub(r"[ \t]{2,}", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


class FetchUrlTool(Tool):
    name = "fetch_url"
    kind = "network"
    read_only = True
    description = (
        "Fetch a URL and return it as readable text (HTML is converted to "
        "markdown-ish text, JSON is pretty-printed). Use for docs, changelogs "
        "and API responses."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "http(s) URL."},
            "max_length": {"type": "integer", "description": "Character cap on the returned text."},
        },
        "required": ["url"],
    }

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        return Preview(title=f"Fetch {args.get('url')}", detail="outbound HTTP request", kind="network")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        url = str(args.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return ToolResult.fail("url must start with http:// or https://")

        host = urlparse(url).hostname or ""
        if PRIVATE_HOSTS.match(host):
            return ToolResult.fail(
                f"refusing to fetch a private/loopback address ({host}). "
                "Use bash with curl if you really mean to reach a local service."
            )

        timeout = float(ctx.config.get("web.timeout_sec", 20))
        max_length = int(args.get("max_length") or ctx.config.get("web.max_length", 20000))

        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=timeout, headers={"User-Agent": USER_AGENT}
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            return ToolResult.fail(f"request failed: {exc}")

        content_type = response.headers.get("content-type", "")
        text = response.text

        if "json" in content_type:
            try:
                text = json.dumps(response.json(), indent=2)
            except ValueError:
                pass
        elif "html" in content_type:
            text = html_to_text(text)

        if response.status_code >= 400:
            return ToolResult.fail(
                f"HTTP {response.status_code} from {url}\n{truncate_output(text, 2000)}"
            )

        return ToolResult.succeed(
            f"{url} (HTTP {response.status_code}, {content_type or 'unknown type'})\n\n"
            + truncate_output(text, max_length),
            headline=f"fetched {host}",
        )


class WebSearchTool(Tool):
    name = "web_search"
    kind = "network"
    read_only = True
    description = (
        "Search the web for current information. Returns titles, URLs and "
        "snippets; follow up with fetch_url to read a result in full."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "required": ["query"],
    }

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        return Preview(title=f"Search: {args.get('query')}", detail="outbound HTTP request", kind="network")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult.fail("query is required")

        limit = int(args.get("max_results") or 5)
        endpoint = str(ctx.config.get("web.search_endpoint") or "").strip()
        api_key = str(ctx.config.get("web.search_api_key") or "").strip()

        # Provider chain: an explicit endpoint wins, then Ollama's hosted search
        # if a key is present, then DuckDuckGo -- which needs no key at all, so
        # search always works out of the box.
        if endpoint:
            return await self._custom_search(endpoint, api_key, query, limit, ctx)

        provider = str(ctx.config.get("web.search_provider", "auto")).lower()
        if provider in ("auto", "ollama") and os.environ.get("OLLAMA_API_KEY"):
            result = await self._ollama_search(query, limit)
            if result is not None:
                return result
            if provider == "ollama":
                return ToolResult.fail("Ollama web search failed and no fallback was allowed")

        return await self._duckduckgo(query, limit, ctx)

    async def _ollama_search(self, query: str, limit: int) -> ToolResult | None:
        """ollama.com hosted search. Returns None so callers can fall back."""
        try:
            import ollama

            response = await asyncio.to_thread(ollama.web_search, query, limit)
        except Exception:
            return None

        results = []
        for item in getattr(response, "results", None) or []:
            results.append({
                "title": getattr(item, "title", "") or "",
                "url": getattr(item, "url", "") or "",
                "snippet": (getattr(item, "content", "") or "")[:400],
            })
        if not results:
            return None
        return _render_results(query, results, "ollama")

    async def _custom_search(
        self, endpoint: str, api_key: str, query: str, limit: int, ctx: ToolContext
    ) -> ToolResult:
        joiner = "&" if "?" in endpoint else "?"
        url = f"{endpoint}{joiner}{urlencode({'q': query, 'n': limit})}"
        headers = {"User-Agent": USER_AGENT}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            async with httpx.AsyncClient(timeout=ctx.config.get("web.timeout_sec", 20)) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ToolResult.fail(f"search failed: {exc}")

        results = payload.get("results", payload) if isinstance(payload, dict) else payload
        return ToolResult.succeed(
            json.dumps(results[:limit] if isinstance(results, list) else results, indent=2)[:15000],
            headline=f"search: {query}",
        )

    async def _duckduckgo(self, query: str, limit: int, ctx: ToolContext) -> ToolResult:
        """Free, no-API-key search by scraping DuckDuckGo's HTML endpoints.

        Two endpoints are tried because either can start returning a challenge
        page; between them this is reliable enough to depend on.
        """
        timeout = float(ctx.config.get("web.timeout_sec", 20))
        headers = {
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }
        errors: list[str] = []

        for endpoint in ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"):
            try:
                async with httpx.AsyncClient(
                    follow_redirects=True, timeout=timeout, headers=headers
                ) as client:
                    # POST avoids some of the bot checks the GET form trips
                    response = await client.post(endpoint, data={"q": query})
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                errors.append(f"{endpoint}: {exc}")
                continue

            results = _parse_ddg(response.text, limit)
            if results:
                return _render_results(query, results, "duckduckgo")
            errors.append(f"{endpoint}: no parseable results")

        detail = "; ".join(errors)
        return ToolResult.fail(
            f"web search is unavailable right now ({detail}). "
            "Set OLLAMA_API_KEY for Ollama's hosted search, or web.search_endpoint "
            "for your own provider. You can still read a known URL with fetch_url."
        )


def unwrap_ddg_url(url: str) -> str:
    """DuckDuckGo wraps results in //duckduckgo.com/l/?uddg=<encoded>.

    Left as-is those are unusable: no scheme, so fetch_url refuses them, and
    they burn tokens. Pull the real destination back out.
    """
    url = html_module.unescape(url.strip())
    if url.startswith("//"):
        url = "https:" + url
    if "duckduckgo.com/l/" in url and "uddg=" in url:
        try:
            target = parse_qs(urlparse(url).query).get("uddg", [None])[0]
            if target:
                return unquote(target)
        except (ValueError, KeyError):
            pass
    return url


_DDG_HTML = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'class="result__snippet"[^>]*>(.*?)</a>',
    re.S,
)
_DDG_LITE = re.compile(
    r'<a[^>]+class="result-link"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'class="result-snippet"[^>]*>(.*?)</td>',
    re.S,
)


def _parse_ddg(html: str, limit: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for pattern in (_DDG_HTML, _DDG_LITE):
        for match in pattern.finditer(html):
            link, title, snippet = match.groups()
            url = unwrap_ddg_url(link)
            if not url.startswith("http"):
                continue
            results.append({
                "title": html_to_text(title).strip(),
                "url": url,
                "snippet": " ".join(html_to_text(snippet).split())[:350],
            })
            if len(results) >= limit:
                return results
        if results:
            break
    return results


def _render_results(query: str, results: list[dict[str, str]], provider: str) -> ToolResult:
    rendered = "\n\n".join(
        f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}"
        for i, r in enumerate(results, 1)
    )
    return ToolResult.succeed(
        f"Results for {query!r} (via {provider}). "
        f"Use fetch_url on any of these to read the full page.\n\n{rendered}",
        headline=f"search: {query} ({len(results)})",
    )

"""Network tools: fetch_url and web_search."""

from __future__ import annotations

import html as html_module
import json
import re
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from .base import Preview, Tool, ToolContext, ToolResult, truncate_output

USER_AGENT = "OllamaCoder/0.3 (+https://github.com/lalomorales22/ollama-coder)"

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

        if endpoint:
            return await self._custom_search(endpoint, api_key, query, limit, ctx)
        return await self._duckduckgo(query, limit, ctx)

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
        """No-API-key fallback via the DuckDuckGo HTML endpoint."""
        url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=ctx.config.get("web.timeout_sec", 20),
                headers={"User-Agent": USER_AGENT},
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return ToolResult.fail(
                f"search failed: {exc}. Configure web.search_endpoint for a dedicated provider."
            )

        results: list[dict[str, str]] = []
        pattern = re.compile(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
            r'class="result__snippet"[^>]*>(.*?)</a>',
            re.S,
        )
        for match in pattern.finditer(response.text):
            link, title, snippet = match.groups()
            results.append({
                "title": html_to_text(title),
                "url": html_module.unescape(link),
                "snippet": html_to_text(snippet)[:300],
            })
            if len(results) >= limit:
                break

        if not results:
            return ToolResult.succeed(
                f"no results for {query!r}", headline=f"search: {query} (0)"
            )

        rendered = "\n\n".join(
            f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}"
            for i, r in enumerate(results, 1)
        )
        return ToolResult.succeed(
            f"Results for {query!r}:\n\n{rendered}", headline=f"search: {query} ({len(results)})"
        )

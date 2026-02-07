"""Web tools — search and fetch (adapted from nanobot)."""

from __future__ import annotations

import os
import re

import httpx
from langchain_core.tools import tool

from graphbot.core.config.schema import Config

SEARCH_TIMEOUT = 10
FETCH_TIMEOUT = 30
MAX_REDIRECTS = 5


def make_web_tools(config: Config) -> list:
    """Create web tools. web_search requires BRAVE_API_KEY env var."""

    @tool
    async def web_search(query: str, count: int = 5) -> str:
        """Search the web using Brave Search API. Returns titles, URLs, and snippets."""
        api_key = config.tools.web.search_api_key or os.environ.get("BRAVE_API_KEY", "")
        if not api_key:
            return "Web search unavailable: BRAVE_API_KEY not configured."

        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
            try:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": min(count, 10)},
                    headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                return f"Search error: {e}"

        results = data.get("web", {}).get("results", [])
        if not results:
            return f"No results found for: {query}"

        lines = [f"Results for: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', 'No title')}")
            lines.append(f"   {r.get('url', '')}")
            desc = r.get("description", "")
            if desc:
                lines.append(f"   {desc}")
            lines.append("")
        return "\n".join(lines)

    @tool
    async def web_fetch(url: str, max_chars: int = 50_000) -> str:
        """Fetch a web page and return its content as text. HTML is converted to readable text."""
        if not url.startswith(("http://", "https://")):
            return "Invalid URL: must start with http:// or https://"

        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT, follow_redirects=True, max_redirects=MAX_REDIRECTS
        ) as client:
            try:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "GraphBot/1.0"},
                )
                resp.raise_for_status()
            except Exception as e:
                return f"Fetch error: {e}"

        content_type = resp.headers.get("content-type", "")

        if "json" in content_type:
            text = resp.text
        elif "html" in content_type:
            text = _html_to_text(resp.text)
        else:
            text = resp.text

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n... truncated ({len(text)} chars total)"

        return text

    return [web_search, web_fetch]


def _html_to_text(html: str) -> str:
    """Simple HTML to text conversion (no external dependency)."""
    # Remove script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Convert headings
    text = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"\n\n\1\n", text, flags=re.IGNORECASE)
    # Convert paragraphs and divs to newlines
    text = re.sub(r"</(p|div|tr|li)>", "\n", text, flags=re.IGNORECASE)
    # Convert br to newline
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # Strip all remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()

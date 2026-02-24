"""Web tools — search and fetch with multi-provider fallback."""

from __future__ import annotations

import asyncio
import json
import os
import re

import httpx
from langchain_core.tools import tool
from loguru import logger

from graphbot.core.config.schema import Config

SEARCH_TIMEOUT = 10
FETCH_TIMEOUT = 30
MAX_REDIRECTS = 5

def make_web_tools(config: Config) -> list:
    """Create web tools.

    web_search fallback chain: DuckDuckGo → Tavily → Moonshot → Brave.
    """

    @tool
    async def web_search(query: str, count: int = 5) -> str:
        """Search the web for real-time information like news, scores, prices.

        Parameters
        ----------
        query : str
            Search query string, e.g. 'Istanbul weather today', 'Bitcoin price',
            'Fenerbahce match result'.
        count : int
            Max number of results to return (default 5).
        """
        # Strategy 1: DuckDuckGo (free, no API key)
        ddg_result = await _ddg_search(query, count)
        if ddg_result:
            logger.debug(f"web_search engine=duckduckgo query={query!r}")
            return ddg_result

        # Strategy 2: Tavily (free 1000/month, AI-optimized)
        tavily_key = os.environ.get("TAVILY_API_KEY", "")
        if tavily_key:
            tavily_result = await _tavily_search(query, tavily_key, count)
            if tavily_result:
                logger.debug(f"web_search engine=tavily query={query!r}")
                return tavily_result

        # Strategy 3: Moonshot $web_search
        moonshot_key = os.environ.get("MOONSHOT_API_KEY", "")
        if moonshot_key:
            logger.debug(f"web_search engine=moonshot query={query!r}")
            return await _moonshot_search(query, moonshot_key)

        # Strategy 4: Brave Search API
        brave_key = config.tools.web.search_api_key or os.environ.get("BRAVE_API_KEY", "")
        if brave_key:
            logger.debug(f"web_search engine=brave query={query!r}")
            return await _brave_search(query, brave_key, count)

        return "Web search unavailable: all search providers failed."

    shortcuts = config.tools.web.fetch_shortcuts
    shortcut_names = ", ".join(sorted(shortcuts.keys())) if shortcuts else "none configured"
    shortcut_examples = " | ".join(
        f'url="{k}"' for k in list(shortcuts.keys())[:3]
    ) if shortcuts else ""
    docstring = (
        "Fetch a web page or shortcut tag and return content as text.\n\n"
        f"Available shortcuts: {shortcut_names}.\n"
        f"Usage: pass the shortcut name as the url parameter, e.g. {shortcut_examples}.\n"
        "For weather, use web_fetch(url='weather:istanbul'). "
        "For any URL, pass the full URL as url parameter."
    )

    @tool
    async def web_fetch(url: str, max_chars: int = 50_000) -> str:
        """Fetch a web page or shortcut tag and return content as text."""
        # Resolve shortcut tags from config
        resolved = shortcuts.get(url.lower())
        if resolved:
            logger.debug(f"web_fetch shortcut={url!r} → {resolved}")
            url = resolved
        elif not url.startswith(("http://", "https://")):
            return f"Unknown shortcut '{url}'. Available shortcuts: {shortcut_names}"

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

    # Override docstring with dynamic shortcut list
    web_fetch.description = docstring

    return [web_search, web_fetch]


# ── Search providers ─────────────────────────────────────────


async def _ddg_search(query: str, count: int = 5) -> str | None:
    """Search using DuckDuckGo (free, no API key)."""
    try:
        from duckduckgo_search import DDGS

        def _search():
            return DDGS().text(query, max_results=min(count, 10))

        results = await asyncio.to_thread(_search)

        if not results:
            return None

        lines = [f"Results for: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', 'No title')}")
            lines.append(f"   {r.get('href', '')}")
            body = r.get("body", "")
            if body:
                lines.append(f"   {body}")
            lines.append("")
        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"DuckDuckGo search failed: {e}")
        return None


async def _tavily_search(query: str, api_key: str, count: int = 5) -> str | None:
    """Search using Tavily API (AI-optimized results)."""
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
        try:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": min(count, 10),
                    "search_depth": "basic",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"Tavily search failed: {e}")
            return None

    results = data.get("results", [])
    if not results:
        return None

    lines = [f"Results for: {query}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title', 'No title')}")
        lines.append(f"   {r.get('url', '')}")
        content = r.get("content", "")
        if content:
            lines.append(f"   {content[:200]}")
        lines.append("")
    return "\n".join(lines)


async def _moonshot_search(query: str, api_key: str) -> str:
    """Use Moonshot $web_search builtin as a search engine."""
    messages = [
        {"role": "user", "content": query},
    ]
    tools = [{"type": "builtin_function", "function": {"name": "$web_search"}}]

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            for _ in range(5):
                resp = await client.post(
                    "https://api.moonshot.ai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "kimi-k2-turbo-preview",
                        "messages": messages,
                        "temperature": 0.6,
                        "tools": tools,
                        "extra_body": {"thinking": {"type": "disabled"}},
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]

                if choice["finish_reason"] == "tool_calls":
                    msg = choice["message"]
                    messages.append(msg)
                    for tc in msg.get("tool_calls", []):
                        args = json.loads(tc["function"]["arguments"])
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "name": tc["function"]["name"],
                            "content": json.dumps(args),
                        })
                else:
                    return choice["message"].get("content", "No results.")

        except Exception as e:
            logger.warning(f"Moonshot search error: {e}")
            return f"Search error: {e}"

    return "Search failed: max iterations reached."


async def _brave_search(query: str, api_key: str, count: int = 5) -> str:
    """Search using Brave Search API."""
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


# ── HTML helper ──────────────────────────────────────────────


def _html_to_text(html: str) -> str:
    """Simple HTML to text conversion (no external dependency)."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"\n\n\1\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|tr|li)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()

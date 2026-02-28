"""Search tools — item search, detail, and time utility."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

if TYPE_CHECKING:
    from graphbot.rag.retriever import SemanticRetriever


def make_search_tools(retriever: SemanticRetriever | None = None) -> list:
    """Create search tools. Uses retriever if provided, otherwise mock."""

    @tool
    def search_items(query: str, max_results: int = 5) -> str:
        """Search the knowledge base for items matching the query."""
        if retriever is None or not retriever.ready:
            return (
                f"Search results for '{query}' (mock):\n"
                "No items in knowledge base yet. RAG will be connected in a future update."
            )

        results = retriever.search(query, top_k=max_results)
        return retriever.format_results(results)

    @tool
    def get_item_detail(item_id: str) -> str:
        """Get detailed information about a specific item by ID."""
        if retriever is None or not retriever.ready:
            return f"Item '{item_id}' not found. Knowledge base not yet configured."

        item: dict[str, Any] | None = retriever.get_by_id(item_id)
        if item is None:
            return f"Item '{item_id}' not found."

        # Format item fields as readable text
        lines = [f"{k}: {v}" for k, v in item.items()]
        return "\n".join(lines)

    @tool
    def get_current_time(timezone_name: str = "Europe/Istanbul") -> str:
        """Get the current date and time. Returns ISO format with day of week.

        Use this tool whenever you need to know the current time, date,
        or day of week. Default timezone is Europe/Istanbul (UTC+3).
        """
        tz_offsets = {
            "Europe/Istanbul": timedelta(hours=3),
            "UTC": timedelta(hours=0),
            "Europe/London": timedelta(hours=0),
            "Europe/Berlin": timedelta(hours=1),
            "US/Eastern": timedelta(hours=-5),
            "US/Pacific": timedelta(hours=-8),
        }
        offset = tz_offsets.get(timezone_name, timedelta(hours=3))
        tz = timezone(offset)
        now = datetime.now(tz)
        days_tr = {
            "Monday": "Pazartesi", "Tuesday": "Salı",
            "Wednesday": "Çarşamba", "Thursday": "Perşembe",
            "Friday": "Cuma", "Saturday": "Cumartesi",
            "Sunday": "Pazar",
        }
        day_en = now.strftime("%A")
        day_tr = days_tr.get(day_en, day_en)
        return (
            f"{now.strftime('%Y-%m-%d %H:%M:%S')} ({day_tr}), "
            f"timezone: {timezone_name}"
        )

    return [search_items, get_item_detail, get_current_time]

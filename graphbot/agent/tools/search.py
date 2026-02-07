"""Search tools — item search and detail (mock data, RAG in Faz 9)."""

from __future__ import annotations

from langchain_core.tools import tool


def make_search_tools() -> list:
    """Create search tools with mock data. Will connect to RAG in Faz 9."""

    @tool
    def search_items(query: str, max_results: int = 5) -> str:
        """Search the knowledge base for items matching the query."""
        # Mock implementation — returns placeholder
        return (
            f"Search results for '{query}' (mock):\n"
            "No items in knowledge base yet. RAG will be connected in a future update."
        )

    @tool
    def get_item_detail(item_id: str) -> str:
        """Get detailed information about a specific item by ID."""
        # Mock implementation
        return f"Item '{item_id}' not found. Knowledge base not yet configured."

    return [search_items, get_item_detail]

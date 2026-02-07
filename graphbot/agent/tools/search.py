"""Search tools — item search and detail (mock or real RAG)."""

from __future__ import annotations

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

    return [search_items, get_item_detail]

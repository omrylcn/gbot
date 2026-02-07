"""Memory tools — user notes, context, activities, favorites."""

from __future__ import annotations

from langchain_core.tools import tool

from graphbot.memory.store import MemoryStore


def make_memory_tools(db: MemoryStore) -> list:
    """Create memory tools closed over db."""

    @tool
    def save_user_note(user_id: str, note: str) -> str:
        """Save a learned fact or note about the user for future reference."""
        db.add_note(user_id, note)
        return f"Note saved: {note}"

    @tool
    def get_user_context(user_id: str) -> str:
        """Get full user context including notes, favorites, preferences, and recent activities."""
        ctx = db.get_user_context(user_id)
        return ctx if ctx else "No context found for this user."

    @tool
    def log_activity(user_id: str, item_title: str, item_id: str = "") -> str:
        """Log a user activity (e.g. used an item, completed a task)."""
        db.log_activity(user_id, item_title, item_id=item_id or None)
        return f"Activity logged: {item_title}"

    @tool
    def get_recent_activities(user_id: str, days: int = 7) -> str:
        """Get user's recent activities from the last N days."""
        rows = db.get_recent_activities(user_id, days=days)
        if not rows:
            return "No recent activities."
        lines = []
        for r in rows:
            lines.append(f"- {r['item_title']} ({r['activity_date']})")
        return "\n".join(lines)

    @tool
    def add_favorite(user_id: str, item_id: str, item_title: str) -> str:
        """Add an item to user's favorites list."""
        if db.is_favorite(user_id, item_id):
            return f"'{item_title}' is already in favorites."
        db.add_favorite(user_id, item_id, item_title)
        return f"Added to favorites: {item_title}"

    @tool
    def get_favorites(user_id: str) -> str:
        """Get user's favorite items."""
        favs = db.get_favorites(user_id)
        if not favs:
            return "No favorites yet."
        lines = [f"- {f['item_title']}" for f in favs]
        return "\n".join(lines)

    @tool
    def remove_favorite(user_id: str, item_id: str) -> str:
        """Remove an item from user's favorites."""
        db.remove_favorite(user_id, item_id)
        return "Removed from favorites."

    return [
        save_user_note,
        get_user_context,
        log_activity,
        get_recent_activities,
        add_favorite,
        get_favorites,
        remove_favorite,
    ]

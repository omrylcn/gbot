"""Memory tools — user notes, context, activities, favorites, preferences."""

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

    @tool
    def set_user_preference(user_id: str, key: str, value: str) -> str:
        """Save a user preference (e.g. language, response_style, theme).

        Parameters
        ----------
        user_id : str
            Target user ID.
        key : str
            Preference key (e.g. 'language', 'tone', 'theme').
        value : str
            Preference value.
        """
        db.update_preferences(user_id, {key: value})
        return f"Preference saved: {key} = {value}"

    @tool
    def get_user_preferences(user_id: str) -> str:
        """Get all saved preferences for a user."""
        prefs = db.get_preferences(user_id)
        if not prefs:
            return "No preferences saved yet."
        lines = [f"- {k}: {v}" for k, v in prefs.items()]
        return "User preferences:\n" + "\n".join(lines)

    @tool
    def remove_user_preference(user_id: str, key: str) -> str:
        """Remove a specific user preference by key."""
        removed = db.remove_preference(user_id, key)
        if not removed:
            return f"Preference '{key}' not found."
        return f"Preference removed: {key}"

    return [
        save_user_note,
        get_user_context,
        add_favorite,
        get_favorites,
        remove_favorite,
        set_user_preference,
        get_user_preferences,
        remove_user_preference,
    ]

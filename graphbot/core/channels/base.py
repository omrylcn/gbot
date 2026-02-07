"""Channel base — common helpers for cross-channel identity and access control."""

from __future__ import annotations

from graphbot.core.config.schema import ChannelsConfig, Config
from graphbot.memory.store import MemoryStore


def resolve_or_create_user(
    db: MemoryStore, channel: str, channel_user_id: str
) -> str:
    """Resolve an existing user by channel identity, or create a new one.

    Returns user_id (existing or newly created).
    """
    user_id = db.resolve_user(channel, channel_user_id)
    if user_id:
        return user_id

    # New user — derive user_id from channel identity
    new_user_id = f"{channel}_{channel_user_id}"
    db.get_or_create_user(new_user_id)
    db.link_channel(new_user_id, channel, channel_user_id)
    return new_user_id


def check_allowlist(
    channels_config: ChannelsConfig, channel: str, sender_id: str
) -> bool:
    """Check if sender is in the channel's allow_from list.

    Empty allow_from list means allow everyone.
    """
    channel_cfg = getattr(channels_config, channel, None)
    if channel_cfg is None:
        return False

    allow_from = getattr(channel_cfg, "allow_from", [])
    if not allow_from:
        return True  # Empty list = no restriction

    return sender_id in allow_from


def resolve_user_strict(
    db: MemoryStore, channel: str, channel_user_id: str
) -> str | None:
    """Resolve channel identity to user_id. Returns None if not found.

    Unlike resolve_or_create_user, this does NOT auto-create users.
    Used when owner is configured (DB-based access control).
    """
    return db.resolve_user(channel, channel_user_id)


def is_owner_mode(config: Config) -> bool:
    """Check if owner-based access control is active."""
    return config.assistant.owner is not None

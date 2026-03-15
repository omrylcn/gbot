"""Channel handlers — webhook endpoints for messaging platforms."""

from gbot.core.channels.base import check_allowlist, resolve_or_create_user

__all__ = ["check_allowlist", "resolve_or_create_user"]

"""RBAC permissions — load roles.yaml and resolve tool/context access per role."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

_ROLES_DATA: dict[str, Any] | None = None


def _load_roles_yaml(path: str | Path | None = None) -> dict[str, Any]:
    """Load and cache roles.yaml. Returns empty dict if file missing."""
    global _ROLES_DATA
    if _ROLES_DATA is not None:
        return _ROLES_DATA

    if path is None:
        path = Path("roles.yaml")
    else:
        path = Path(path)

    if not path.exists():
        logger.warning(f"roles.yaml not found at {path}, RBAC disabled (all tools allowed)")
        _ROLES_DATA = {}
        return _ROLES_DATA

    with open(path, encoding="utf-8") as f:
        _ROLES_DATA = yaml.safe_load(f) or {}
    logger.info(f"Loaded roles.yaml: {list((_ROLES_DATA.get('roles') or {}).keys())} roles")
    return _ROLES_DATA


def reset_cache() -> None:
    """Clear cached roles data (for testing)."""
    global _ROLES_DATA
    _ROLES_DATA = None


def get_default_role(path: str | Path | None = None) -> str:
    """Get the default role for new users."""
    data = _load_roles_yaml(path)
    return data.get("default_role", "guest")


def get_allowed_tools(role: str, path: str | Path | None = None) -> set[str] | None:
    """Resolve allowed tool names for a role.

    Returns
    -------
    set[str] | None
        Set of allowed tool names, or None if RBAC is disabled
        (roles.yaml missing → no filtering).
    """
    data = _load_roles_yaml(path)
    if not data:
        return None  # RBAC disabled — allow all

    roles = data.get("roles", {})
    role_def = roles.get(role)
    if role_def is None:
        logger.warning(f"Unknown role '{role}', denying all tools")
        return set()

    tool_groups = data.get("tool_groups", {})
    allowed: set[str] = set()
    for group_name in role_def.get("tool_groups", []):
        tools_in_group = tool_groups.get(group_name, [])
        allowed.update(tools_in_group)

    return allowed


def get_context_layers(role: str, path: str | Path | None = None) -> set[str] | None:
    """Resolve allowed context layers for a role.

    Returns
    -------
    set[str] | None
        Set of allowed layer names, or None if RBAC is disabled.
    """
    data = _load_roles_yaml(path)
    if not data:
        return None  # RBAC disabled — all layers

    roles = data.get("roles", {})
    role_def = roles.get(role)
    if role_def is None:
        return {"identity", "runtime", "role"}  # minimal fallback

    return set(role_def.get("context_layers", []))


def get_max_sessions(role: str, path: str | Path | None = None) -> int:
    """Get max concurrent sessions for a role. 0 = unlimited."""
    data = _load_roles_yaml(path)
    if not data:
        return 0

    roles = data.get("roles", {})
    role_def = roles.get(role)
    if role_def is None:
        return 1  # unknown role → restrictive

    return role_def.get("max_sessions", 0)

"""Shared tool registry utilities for background agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import BaseTool
from loguru import logger

if TYPE_CHECKING:
    from graphbot.agent.tools import ToolRegistry
    from graphbot.core.config.schema import Config
    from graphbot.memory.store import MemoryStore


# Groups excluded from background agents (unsafe or meta-tools)
_UNSAFE_GROUPS = frozenset({"filesystem", "shell", "delegation"})


def build_background_registry(registry: ToolRegistry) -> dict[str, BaseTool]:
    """Build name -> tool object mapping for background agents.

    Extracts a safe subset from the main ToolRegistry,
    excluding filesystem, shell, delegation, and scheduling tools.

    Parameters
    ----------
    registry : ToolRegistry
        Main tool registry.

    Returns
    -------
    dict[str, BaseTool]
        Background-safe tool name to object mapping.
    """
    result: dict[str, BaseTool] = {}
    for name, info in registry._tools.items():
        if info.group not in _UNSAFE_GROUPS and info.available:
            result[name] = info.tool
    return result


def build_background_tool_registry(
    config: "Config", db: "MemoryStore | None" = None,
) -> dict[str, BaseTool]:
    """Build name -> tool mapping for background agents (standalone).

    This creates tools independently, without requiring the main ToolRegistry.
    Used by SubagentWorker which is initialized before the main registry.

    Parameters
    ----------
    config : Config
        Application config.
    db : MemoryStore, optional
        Database store. If provided, memory/search/messaging tools included.
    """
    from graphbot.agent.tools.web import make_web_tools

    registry: dict[str, BaseTool] = {}
    for t in make_web_tools(config):
        registry[t.name] = t

    if db:
        from graphbot.agent.tools.memory_tools import make_memory_tools
        from graphbot.agent.tools.messaging import make_messaging_tools
        from graphbot.agent.tools.search import make_search_tools

        for t in make_memory_tools(db):
            registry[t.name] = t
        for t in make_search_tools():
            registry[t.name] = t
        for t in make_messaging_tools(config, db, background=True):
            registry[t.name] = t

    return registry


def resolve_tools(
    registry: dict[str, BaseTool],
    tool_names: list[str] | None,
    default: list[str] | None = None,
) -> list[BaseTool]:
    """Resolve tool name strings to actual tool objects.

    Parameters
    ----------
    tool_names : list[str] or None
        None with default -> default tools.
        None without default -> empty list.
        Explicit list -> resolve from registry.
    """
    if tool_names is None:
        if default:
            return [registry[n] for n in default if n in registry]
        return []

    resolved = []
    for name in tool_names:
        if name in registry:
            resolved.append(registry[name])
        else:
            logger.warning(f"Tool '{name}' not found in registry, skipping")
    return resolved


def get_tool_catalog(registry: dict[str, BaseTool]) -> str:
    """Build human-readable tool catalog for delegation planner prompt.

    Returns
    -------
    str
        Tool name + full description (up to 300 chars).
    """
    lines = []
    for name, t in registry.items():
        desc = (t.description or "").strip()
        if len(desc) > 300:
            desc = desc[:300] + "..."
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)

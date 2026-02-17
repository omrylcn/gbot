"""Shared tool registry for background agents (subagent + cron)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import BaseTool
from loguru import logger

if TYPE_CHECKING:
    from graphbot.core.config.schema import Config
    from graphbot.memory.store import MemoryStore


def build_background_tool_registry(
    config: Config, db: MemoryStore | None = None
) -> dict[str, BaseTool]:
    """Build name -> tool object mapping for background agents.

    Includes web, memory, search, and messaging tools.
    Excludes meta/unsafe tools (delegate, cron, reminder, filesystem, shell).
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
        for t in make_messaging_tools(config, db):
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
        None with default → default tools.
        None without default → empty list.
        Explicit list → resolve from registry.
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
        One line per tool: ``- tool_name: first line of description``.
    """
    lines = []
    for name, t in registry.items():
        desc = (t.description or "").split("\n")[0]
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)

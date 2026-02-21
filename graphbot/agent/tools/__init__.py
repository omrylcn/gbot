"""Tool system — ToolRegistry and factory that creates all agent tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool
from loguru import logger

from graphbot.agent.tools.cron_tool import make_cron_tools
from graphbot.agent.tools.delegate import make_delegate_tools
from graphbot.agent.tools.reminder import make_reminder_tools
from graphbot.agent.tools.filesystem import make_filesystem_tools
from graphbot.agent.tools.memory_tools import make_memory_tools
from graphbot.agent.tools.messaging import make_messaging_tools
from graphbot.agent.tools.search import make_search_tools
from graphbot.agent.tools.shell import make_shell_tools
from graphbot.agent.tools.web import make_web_tools
from graphbot.core.config.schema import Config
from graphbot.memory.store import MemoryStore

if TYPE_CHECKING:
    from graphbot.agent.delegation import DelegationPlanner
    from graphbot.core.background.worker import SubagentWorker
    from graphbot.core.cron.scheduler import CronScheduler


@dataclass
class ToolInfo:
    """Metadata for a registered tool."""

    tool: BaseTool
    group: str
    requires: list[str] = field(default_factory=list)
    available: bool = True


class ToolRegistry:
    """Central tool registry — single source of truth for tool metadata.

    Each factory function (make_*_tools) registers its tools under a group name.
    roles.yaml only defines role -> groups mapping; tool names are resolved
    automatically from this registry.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolInfo] = {}
        self._groups: dict[str, list[str]] = {}

    def register_group(
        self,
        group: str,
        tools: list,
        requires: list[str] | None = None,
    ) -> None:
        """Register a list of tools under a group name.

        If the group was previously registered as unavailable,
        replaces those placeholder entries.
        """
        # Clear previous entries (e.g. unavailable placeholders)
        self._groups[group] = []

        for t in tools:
            info = ToolInfo(
                tool=t, group=group, requires=requires or [], available=True,
            )
            self._tools[t.name] = info
            self._groups[group].append(t.name)

    def register_unavailable(
        self,
        group: str,
        tool_names: list[str],
        requires: list[str],
    ) -> None:
        """Register tools that exist but are unavailable (missing deps).

        These appear in the group mapping but cannot be used at runtime.
        """
        for name in tool_names:
            self._groups.setdefault(group, []).append(name)

    def get_all_tools(self) -> list:
        """Return all available tool objects."""
        return [info.tool for info in self._tools.values() if info.available]

    def get_group_tool_names(self, group: str) -> list[str]:
        """Return tool names in a group (including unavailable)."""
        return list(self._groups.get(group, []))

    def get_tools_for_groups(self, groups: list[str]) -> set[str]:
        """Resolve groups to a flat set of available tool names."""
        names: set[str] = set()
        for g in groups:
            for name in self._groups.get(g, []):
                if name in self._tools and self._tools[name].available:
                    names.add(name)
        return names

    def get_catalog(self) -> list[dict[str, Any]]:
        """Full catalog for admin API introspection."""
        result = []
        for name in sorted(self._tools):
            info = self._tools[name]
            result.append({
                "name": name,
                "group": info.group,
                "description": (info.tool.description or "").split("\n")[0],
                "available": info.available,
                "requires": info.requires,
            })
        return result

    def get_groups_summary(self) -> dict[str, list[str]]:
        """Return group name to tool names mapping."""
        return {g: list(names) for g, names in self._groups.items()}

    def validate_roles(self, roles_data: dict) -> list[str]:
        """Validate roles.yaml group references against registry.

        Returns list of warning messages (empty if all valid).
        """
        warnings = []
        known_groups = set(self._groups.keys())
        for role_name, role_cfg in roles_data.get("roles", {}).items():
            for group in role_cfg.get("tool_groups", []):
                if group not in known_groups:
                    warnings.append(
                        f"Role '{role_name}' references unknown group '{group}'"
                    )
        return warnings

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# ── Scheduling tool names (for unavailable registration) ──────

_SCHEDULING_TOOLS = [
    "add_cron_job", "list_cron_jobs", "remove_cron_job", "create_alert",
    "create_reminder", "list_reminders", "cancel_reminder",
]
_DELEGATION_TOOLS = ["delegate"]


def make_tools(
    config: Config,
    db: MemoryStore,
    scheduler: CronScheduler | None = None,
    worker: SubagentWorker | None = None,
    planner: DelegationPlanner | None = None,
) -> ToolRegistry:
    """Create all agent tools and return a ToolRegistry.

    Parameters
    ----------
    config : Config
        Application config.
    db : MemoryStore
        Database store.
    scheduler : CronScheduler, optional
        If provided, scheduling tools are available.
    worker : SubagentWorker, optional
        If provided, delegation tool is available.
    planner : DelegationPlanner, optional
        Delegation planner for the delegate tool.

    Returns
    -------
    ToolRegistry
        Registry with all tools registered under their groups.
    """
    registry = ToolRegistry()

    # Build RAG retriever if configured
    retriever = None
    if config.rag is not None:
        try:
            from graphbot.rag.retriever import SemanticRetriever

            retriever = SemanticRetriever(config.rag)
        except ImportError:
            logger.warning("RAG deps not installed (faiss-cpu, sentence-transformers)")

    # Static tools (always available)
    registry.register_group("memory", make_memory_tools(db))
    registry.register_group("search", make_search_tools(retriever))
    registry.register_group("filesystem", make_filesystem_tools(config))
    registry.register_group("shell", make_shell_tools(config))
    registry.register_group("web", make_web_tools(config))
    registry.register_group("messaging", make_messaging_tools(config, db))

    # Dynamic tools (conditionally available)
    if scheduler:
        registry.register_group(
            "scheduling",
            make_cron_tools(scheduler) + make_reminder_tools(scheduler),
            requires=["scheduler"],
        )
    else:
        registry.register_unavailable(
            "scheduling", _SCHEDULING_TOOLS, requires=["scheduler"],
        )

    if worker:
        registry.register_group(
            "delegation",
            make_delegate_tools(worker, planner),
            requires=["worker"],
        )
    else:
        registry.register_unavailable(
            "delegation", _DELEGATION_TOOLS, requires=["worker"],
        )

    return registry


__all__ = ["ToolRegistry", "ToolInfo", "make_tools"]

"""Tool system — factory that creates all agent tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from graphbot.agent.tools.cron_tool import make_cron_tools
from graphbot.agent.tools.delegate import make_delegate_tools
from graphbot.agent.tools.reminder import make_reminder_tools
from graphbot.agent.tools.filesystem import make_filesystem_tools
from graphbot.agent.tools.memory_tools import make_memory_tools
from graphbot.agent.tools.search import make_search_tools
from graphbot.agent.tools.shell import make_shell_tools
from graphbot.agent.tools.web import make_web_tools
from graphbot.core.config.schema import Config
from graphbot.memory.store import MemoryStore

if TYPE_CHECKING:
    from graphbot.agent.delegation import DelegationPlanner
    from graphbot.core.background.worker import SubagentWorker
    from graphbot.core.cron.scheduler import CronScheduler

def make_tools(
    config: Config,
    db: MemoryStore,
    scheduler: CronScheduler | None = None,
    worker: SubagentWorker | None = None,
    planner: DelegationPlanner | None = None,
) -> list:
    """Create all agent tools from config and db."""
    # Build RAG retriever if configured
    retriever = None
    if config.rag is not None:
        try:
            from graphbot.rag.retriever import SemanticRetriever

            retriever = SemanticRetriever(config.rag)
        except ImportError:
            logger.warning("RAG deps not installed (faiss-cpu, sentence-transformers)")

    tools: list = []
    tools += make_memory_tools(db)
    tools += make_search_tools(retriever)
    tools += make_filesystem_tools(config)
    tools += make_shell_tools(config)
    tools += make_web_tools(config)
    tools += make_delegate_tools(worker, planner)
    tools += make_cron_tools(scheduler)
    tools += make_reminder_tools(scheduler)
    return tools


__all__ = ["make_tools"]

"""Delegate tool — async subagent delegation via SubagentWorker."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from graphbot.core.background.worker import SubagentWorker


def make_delegate_tools(worker: SubagentWorker | None = None) -> list:
    """Create delegate tools. Returns empty list if no worker provided."""
    if worker is None:
        return []

    @tool
    async def delegate(user_id: str, task: str, channel: str = "api") -> str:
        """Delegate a task to a background subagent. Returns task_id for tracking."""
        try:
            task_id = worker.spawn(user_id, task, channel)
            return f"Task delegated: {task_id}"
        except Exception as e:
            return f"Failed to delegate task: {e}"

    return [delegate]

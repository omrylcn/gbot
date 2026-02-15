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
    async def delegate(
        user_id: str,
        task: str,
        channel: str = "api",
        tools: list[str] | None = None,
        model: str | None = None,
    ) -> str:
        """Delegate a task to a background subagent (LightAgent).

        The subagent runs in the background with an isolated context.
        Choose the right tools and model for the task complexity.

        Parameters
        ----------
        user_id : str
            User who requested the task.
        task : str
            Task description for the subagent.
        channel : str
            Delivery channel for the result.
        tools : list[str], optional
            Tool names the subagent needs.  Available: web_search, web_fetch,
            search_items, save_user_note.  Default: [web_search, web_fetch].
        model : str, optional
            Model for the subagent.  Use cheaper models for simple tasks.
            Default: config model.
        """
        try:
            task_id = worker.spawn(
                user_id, task, channel, tools=tools, model=model,
            )
            return f"Task delegated: {task_id}"
        except Exception as e:
            return f"Failed to delegate task: {e}"

    return [delegate]

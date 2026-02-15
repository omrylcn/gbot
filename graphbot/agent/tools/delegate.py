"""Delegate tool — async subagent delegation via SubagentWorker."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from graphbot.agent.delegation import DelegationPlanner
    from graphbot.core.background.worker import SubagentWorker


def make_delegate_tools(
    worker: SubagentWorker | None = None,
    planner: DelegationPlanner | None = None,
) -> list:
    """Create delegate tools. Returns empty list if no worker provided."""
    if worker is None:
        return []

    @tool
    async def delegate(user_id: str, task: str, channel: str = "api") -> str:
        """Delegate a task to a background subagent.

        A planner automatically decides which tools, prompt, and model
        the subagent needs. Just describe the task clearly.

        Parameters
        ----------
        user_id : str
            User who requested the task.
        task : str
            Task description for the subagent.
        channel : str
            Delivery channel for the result.
        """
        try:
            if planner:
                plan = await planner.plan(task)
                task_id = worker.spawn(
                    user_id, task, channel,
                    tools=plan["tools"],
                    prompt=plan["prompt"],
                    model=plan["model"],
                )
            else:
                task_id = worker.spawn(user_id, task, channel)
            return f"Task delegated: {task_id}"
        except Exception as e:
            return f"Failed to delegate task: {e}"

    return [delegate]

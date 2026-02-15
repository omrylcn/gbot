"""SubagentWorker — async background task spawner with LightAgent execution."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from graphbot.core.config.schema import Config
    from graphbot.memory.store import MemoryStore

# Default system prompt for delegated tasks
_DELEGATE_PROMPT = (
    "You are a background task agent. Complete the given task thoroughly "
    "and return a clear, concise result. Do not ask follow-up questions."
)


class SubagentWorker:
    """Manages background tasks spawned by the delegate tool.

    Uses LightAgent for isolated, cost-effective execution instead of
    the full GraphRunner.  The main agent decides which tools and model
    the subagent needs at delegation time.

    Results are persisted to background_tasks table and
    system_events are created for agent notification.
    """

    def __init__(self, config: Config, db: MemoryStore | None = None):
        self.config = config
        self.db = db
        self._tasks: dict[str, asyncio.Task] = {}

    def spawn(
        self,
        user_id: str,
        task: str,
        channel: str = "api",
        tools: list[str] | None = None,
        model: str | None = None,
    ) -> str:
        """Spawn a background task. Returns task_id.

        Parameters
        ----------
        tools : list[str], optional
            Tool names the subagent should have access to.
            None means default tools (web_search, web_fetch).
        model : str, optional
            Model override.  None uses config default.
        """
        task_id = str(uuid.uuid4())[:8]

        if self.db:
            self.db.create_background_task(task_id, user_id, task)

        bg_task = asyncio.create_task(
            self._run(task_id, user_id, task, channel, tools, model)
        )
        self._tasks[task_id] = bg_task
        bg_task.add_done_callback(lambda _: self._tasks.pop(task_id, None))
        logger.info(f"Subagent spawned: {task_id} — {task[:80]}")
        return task_id

    async def _run(
        self,
        task_id: str,
        user_id: str,
        task: str,
        channel: str,
        tool_names: list[str] | None = None,
        model: str | None = None,
    ) -> None:
        """Execute a background task via LightAgent (isolated, lightweight)."""
        try:
            from graphbot.agent.light import LightAgent

            tools = self._resolve_tools(tool_names)
            agent = LightAgent(
                config=self.config,
                prompt=_DELEGATE_PROMPT,
                tools=tools,
                model=model,
            )
            response, tokens = await agent.run(task)
            logger.info(
                f"Subagent {task_id} completed ({tokens} tokens): {response[:100]}"
            )

            if self.db:
                self.db.complete_background_task(task_id, result=response)
                event_id = self.db.add_system_event(
                    user_id,
                    source=f"task:{task_id}",
                    event_type="task_completed",
                    payload=response[:500],
                )

                # Try WS push — mark delivered if successful
                ws_manager = getattr(self, "ws_manager", None)
                if ws_manager:
                    sent = await ws_manager.send_event(user_id, {
                        "type": "event",
                        "event_type": "task_completed",
                        "source": f"task:{task_id}",
                        "payload": response[:500],
                    })
                    if sent:
                        self.db.mark_events_delivered([event_id])
                        logger.info(f"Task {task_id} result pushed via WS")
        except Exception as e:
            logger.error(f"Subagent {task_id} failed: {e}")
            if self.db:
                self.db.fail_background_task(task_id, error=str(e))

    def _resolve_tools(self, tool_names: list[str] | None) -> list:
        """Resolve tool name strings to actual tool objects.

        Default tools when none specified: web_search, web_fetch.
        """
        from graphbot.agent.tools.web import make_web_tools

        if tool_names is None:
            # Default: web tools for general research
            return make_web_tools(self.config)

        # Build a registry of available tools
        available: dict = {}
        for t in make_web_tools(self.config):
            available[t.name] = t

        if self.db:
            from graphbot.agent.tools.memory_tools import make_memory_tools
            from graphbot.agent.tools.search import make_search_tools

            for t in make_memory_tools(self.db):
                available[t.name] = t
            for t in make_search_tools(self.config, self.db):
                available[t.name] = t

        resolved = []
        for name in tool_names:
            if name in available:
                resolved.append(available[name])
            else:
                logger.warning(f"Tool '{name}' not found for subagent, skipping")
        return resolved

    def get_running_count(self) -> int:
        """Number of currently running tasks."""
        return len(self._tasks)

    async def shutdown(self) -> None:
        """Wait for all running tasks to complete."""
        if not self._tasks:
            return
        logger.info(f"Waiting for {len(self._tasks)} subagent tasks to finish")
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

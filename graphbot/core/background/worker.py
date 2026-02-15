"""SubagentWorker — async background task spawner with DB persistence."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from graphbot.agent.runner import GraphRunner
    from graphbot.memory.store import MemoryStore


class SubagentWorker:
    """Manages background tasks spawned by the delegate tool.

    Results are persisted to background_tasks table and
    system_events are created for agent notification.
    """

    def __init__(self, runner: GraphRunner, db: MemoryStore | None = None):
        self.runner = runner
        self.db = db
        self._tasks: dict[str, asyncio.Task] = {}

    def spawn(self, user_id: str, task: str, channel: str = "api") -> str:
        """Spawn a background task. Returns task_id."""
        task_id = str(uuid.uuid4())[:8]

        if self.db:
            self.db.create_background_task(task_id, user_id, task)

        bg_task = asyncio.create_task(self._run(task_id, user_id, task, channel))
        self._tasks[task_id] = bg_task
        bg_task.add_done_callback(lambda _: self._tasks.pop(task_id, None))
        logger.info(f"Subagent spawned: {task_id} — {task[:80]}")
        return task_id

    async def _run(
        self, task_id: str, user_id: str, task: str, channel: str
    ) -> None:
        """Execute a background task via runner.process()."""
        try:
            response, session_id = await self.runner.process(
                user_id=user_id,
                channel=channel,
                message=task,
            )
            logger.info(f"Subagent {task_id} completed: {response[:100]}")

            if self.db:
                self.db.complete_background_task(task_id, result=response)
                self.db.add_system_event(
                    user_id,
                    source=f"task:{task_id}",
                    event_type="task_completed",
                    payload=response[:500],
                )
        except Exception as e:
            logger.error(f"Subagent {task_id} failed: {e}")
            if self.db:
                self.db.fail_background_task(task_id, error=str(e))

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

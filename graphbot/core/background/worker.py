"""SubagentWorker — async background task spawner with LightAgent execution."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from loguru import logger

from graphbot.agent.tools.registry import build_background_tool_registry, resolve_tools

if TYPE_CHECKING:
    from graphbot.core.config.schema import Config
    from graphbot.memory.store import MemoryStore

# Default system prompt for delegated tasks
_DELEGATE_PROMPT = (
    "You are a background task agent. Your goal is to research and return a clear result.\n"
    "IMPORTANT: Use at most 3-4 tool calls, then summarize your findings. "
    "Do NOT keep searching endlessly. After a few searches, write your final answer."
)


class SubagentWorker:
    """Manages background tasks spawned by the delegate tool.

    Uses LightAgent for isolated, cost-effective execution instead of
    the full GraphRunner.  The delegation planner (or caller) decides
    which tools, prompt, and model the subagent needs.

    Results are persisted to background_tasks table and
    system_events are created for agent notification.
    """

    def __init__(self, config: Config, db: MemoryStore | None = None):
        self.config = config
        self.db = db
        self._tasks: dict[str, asyncio.Task] = {}
        self._registry = build_background_tool_registry(config, db)

    def spawn(
        self,
        user_id: str,
        task: str,
        channel: str = "api",
        tools: list[str] | None = None,
        prompt: str | None = None,
        model: str | None = None,
    ) -> str:
        """Spawn a background task. Returns task_id.

        Parameters
        ----------
        tools : list[str], optional
            Tool names the subagent should have access to.
            None means default tools (web_search, web_fetch).
        prompt : str, optional
            System prompt for the subagent. None uses default.
        model : str, optional
            Model override.  None uses config default.
        """
        task_id = str(uuid.uuid4())[:8]

        if self.db:
            self.db.create_background_task(task_id, user_id, task, fallback_channel=channel)

        bg_task = asyncio.create_task(
            self._run(task_id, user_id, task, channel, tools, prompt, model)
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
        prompt: str | None = None,
        model: str | None = None,
    ) -> None:
        """Execute a background task via LightAgent (isolated, lightweight)."""
        try:
            from graphbot.agent.light import LightAgent

            tools = resolve_tools(
                self._registry, tool_names,
                default=["web_search", "web_fetch"],
            )
            resolved_model = model or self.config.assistant.model
            agent = LightAgent(
                config=self.config,
                prompt=prompt or _DELEGATE_PROMPT,
                tools=tools,
                model=resolved_model,
            )
            response, tokens = await agent.run(task)
            logger.info(
                f"Subagent {task_id} completed ({tokens} tokens): {response[:100]}"
            )

            if self.db:
                self.db.complete_background_task(task_id, result=response)

                # Inject result into user's active session so main agent sees it
                session = self.db.get_active_session(user_id, channel=channel)
                if not session:
                    session = self.db.get_active_session(user_id)
                if session:
                    self.db.add_message(
                        session["session_id"],
                        role="assistant",
                        content=f"[Arka plan araştırma sonucu — task:{task_id}]\n\n{response}",
                    )
                    logger.info(f"Subagent {task_id} result added to session {session['session_id']}")

                event_id = self.db.add_system_event(
                    user_id,
                    source=f"task:{task_id}",
                    event_type="task_completed",
                    payload=response[:2000],
                )

                # Deliver result to user's channel
                delivered = await self._deliver_result(
                    user_id, channel, response[:2000], event_id
                )
                if delivered:
                    logger.info(f"Task {task_id} result delivered via {channel}")
        except Exception as e:
            logger.error(f"Subagent {task_id} failed: {e}")
            if self.db:
                self.db.fail_background_task(task_id, error=str(e))

    async def _deliver_result(
        self, user_id: str, channel: str, text: str, event_id: int
    ) -> bool:
        """Deliver task result to user's channel. Returns True if delivered."""
        # Telegram: send directly
        if channel == "telegram":
            link = self.db.get_channel_link(user_id, "telegram")
            if link:
                chat_id = link["metadata"].get("chat_id")
                if chat_id:
                    from graphbot.core.channels.telegram import send_message

                    logger.debug(
                        f"Sending task result to Telegram: chat_id={chat_id}, "
                        f"token={link['channel_user_id'][:10]}..."
                    )
                    await send_message(link["channel_user_id"], int(chat_id), text)
                    self.db.mark_events_delivered([event_id])
                    return True
                logger.warning(f"No chat_id for user {user_id}")
            else:
                logger.warning(f"No telegram link for user {user_id}")
            return False

        # WhatsApp: send directly
        if channel == "whatsapp":
            from graphbot.core.channels.whatsapp import send_whatsapp_message

            wa_config = self.config.channels.whatsapp
            link = self.db.get_channel_link(user_id, "whatsapp")
            if link and wa_config.enabled:
                chat_id = link["metadata"].get("chat_id")
                if chat_id:
                    await send_whatsapp_message(wa_config, chat_id, f"[gbot] {text}")
                    self.db.mark_events_delivered([event_id])
                    logger.info(f"Task result delivered via WhatsApp to {chat_id}")
                    return True
            return False

        # API/WS channel: try WebSocket push
        ws_manager = getattr(self, "ws_manager", None)
        if ws_manager:
            sent = await ws_manager.send_event(user_id, {
                "type": "event",
                "event_type": "task_completed",
                "source": "task:result",
                "payload": text,
            })
            if sent:
                self.db.mark_events_delivered([event_id])
                logger.info(f"Task result pushed via WebSocket to {user_id}")
                return True

        # Fallback: event saved to DB for polling
        logger.info(f"Task result saved to DB for {user_id} (no active delivery)")
        return False

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

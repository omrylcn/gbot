"""CronScheduler — APScheduler + SQLite bridge for dynamic task scheduling."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from loguru import logger

from gbot.agent.tools.registry import build_background_tool_registry, resolve_tools
from gbot.core.cron.types import BackgroundTask, CronJob
from gbot.memory.store import MemoryStore

if TYPE_CHECKING:
    from gbot.agent.runner import GraphRunner
    from gbot.core.config.schema import Config


def _should_skip(response: str) -> bool:
    """Check if LLM response should be suppressed (SKIP/NO_NOTIFY marker)."""
    if not response or not response.strip():
        return True
    markers = {"SKIP", "[SKIP]", "[NO_NOTIFY]"}
    upper = response.strip().upper()
    return any(upper.startswith(m) or upper.endswith(m) for m in markers)


class CronScheduler:
    """Bridge between SQLite background_tasks and APScheduler.

    Tasks are persisted in SQLite (source of truth) and registered
    with APScheduler for execution. Supports recurring, monitor,
    and delayed (one-shot) execution types.
    """

    def __init__(
        self, db: MemoryStore, runner: GraphRunner, config: Config | None = None
    ):
        self.db = db
        self.runner = runner
        self.config = config
        self._scheduler = AsyncIOScheduler(
            job_defaults={"coalesce": True, "max_instances": 1}
        )
        if config:
            self._registry = build_background_tool_registry(config, db)
        else:
            self._registry = {}

    async def start(self) -> None:
        """Load tasks from SQLite and start the scheduler."""
        # Load recurring/monitor tasks
        recurring = self.db.get_tasks(execution_type="recurring", enabled=True)
        monitor = self.db.get_tasks(execution_type="monitor", enabled=True)
        jobs = recurring + monitor
        for row in jobs:
            task = BackgroundTask(**{k: v for k, v in row.items() if k in BackgroundTask.model_fields})
            if task.cron_expr:
                self._register_task(task)

        # Load pending delayed tasks
        delayed = self.db.get_tasks(execution_type="delayed", status="pending")
        for row in delayed:
            task = BackgroundTask(**{k: v for k, v in row.items() if k in BackgroundTask.model_fields})
            self._register_task(task)

        self._scheduler.start()
        logger.info(
            f"CronScheduler started with {len(jobs)} jobs, {len(delayed)} reminders"
        )

    async def stop(self) -> None:
        """Shutdown the scheduler gracefully."""
        self._scheduler.shutdown(wait=False)
        logger.info("CronScheduler stopped")

    # ── Task management ────────────────────────────────────

    def add_job(
        self,
        user_id: str,
        cron_expr: str,
        message: str,
        channel: str = "api",
        agent_prompt: str | None = None,
        agent_tools: list[str] | None = None,
        agent_model: str | None = None,
        notify_condition: str = "always",
        processor: str = "agent",
        plan_json: str | None = None,
    ) -> CronJob:
        """Create a recurring/monitor task."""
        agent_tools_json = json.dumps(agent_tools) if agent_tools else None
        task_id = str(uuid.uuid4())[:8]
        exec_type = "monitor" if notify_condition == "notify_skip" else "recurring"
        self.db.create_task(
            task_id, user_id, message, execution_type=exec_type,
            processor=processor, channel=channel, cron_expr=cron_expr,
            agent_prompt=agent_prompt, agent_tools=agent_tools_json,
            agent_model=agent_model, notify_condition=notify_condition,
            plan_json=plan_json,
        )
        task = BackgroundTask(
            task_id=task_id, user_id=user_id, cron_expr=cron_expr,
            message=message, channel=channel, execution_type=exec_type,
            agent_prompt=agent_prompt, agent_tools=agent_tools_json,
            agent_model=agent_model, notify_condition=notify_condition,
            processor=processor, plan_json=plan_json,
        )
        self._register_task(task)
        logger.info(f"Task added: {task_id} ({exec_type}, {cron_expr})")
        return task

    def remove_job(self, job_id: str) -> None:
        """Remove a task."""
        self.db.delete_task(job_id)
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass
        logger.info(f"Task removed: {job_id}")

    def list_jobs(self, user_id: str | None = None) -> list[dict]:
        """List recurring/monitor tasks."""
        return self.db.get_cron_jobs(user_id)

    # ── Reminders (delayed tasks) ──────────────────────────

    def add_reminder(
        self,
        user_id: str,
        channel: str,
        delay_seconds: int,
        message: str,
        cron_expr: str | None = None,
        agent_prompt: str | None = None,
        agent_tools: list[str] | None = None,
        processor: str = "static",
        plan_json: str | None = None,
    ) -> dict:
        """Create a delayed (one-shot or recurring) task."""
        agent_tools_json = json.dumps(agent_tools) if agent_tools else None
        run_at = (datetime.now() + timedelta(seconds=delay_seconds)).isoformat()
        task_id = str(uuid.uuid4())[:8]
        exec_type = "recurring" if cron_expr else "delayed"
        self.db.create_task(
            task_id, user_id, message, execution_type=exec_type,
            processor=processor, channel=channel, cron_expr=cron_expr,
            run_at=run_at, agent_prompt=agent_prompt,
            agent_tools=agent_tools_json, plan_json=plan_json,
        )
        task = BackgroundTask(
            task_id=task_id, user_id=user_id, message=message,
            execution_type=exec_type, processor=processor, channel=channel,
            cron_expr=cron_expr, run_at=run_at, agent_prompt=agent_prompt,
            agent_tools=agent_tools_json, plan_json=plan_json,
        )
        self._register_task(task)
        kind = f"recurring ({cron_expr})" if cron_expr else f"one-shot at {run_at}"
        logger.info(f"Reminder added: {task_id} ({kind})")
        return {"reminder_id": task_id, "task_id": task_id, "user_id": user_id,
                "message": message, "channel": channel, "run_at": run_at,
                "cron_expr": cron_expr, "processor": processor}

    def list_reminders(self, user_id: str | None = None) -> list[dict]:
        """List pending delayed tasks."""
        return self.db.get_pending_reminders(user_id)

    def cancel_reminder(self, reminder_id: str) -> bool:
        """Cancel a pending task. Returns True if cancelled."""
        result = self.db.cancel_task(reminder_id)
        try:
            self._scheduler.remove_job(reminder_id)
        except Exception:
            pass
        return result

    # ── APScheduler registration ───────────────────────────

    def _register_task(self, task: BackgroundTask) -> None:
        """Register a task with APScheduler based on its type."""
        try:
            if task.execution_type in ("recurring", "monitor") and task.cron_expr:
                trigger = CronTrigger.from_crontab(task.cron_expr)
            elif task.execution_type == "delayed" and task.run_at:
                run_date = datetime.fromisoformat(task.run_at)
                if run_date < datetime.now():
                    self.db.delete_task(task.task_id)
                    return
                trigger = DateTrigger(run_date=run_date)
            else:
                return

            self._scheduler.add_job(
                self._execute_task,
                trigger=trigger,
                id=task.task_id,
                args=[task],
                replace_existing=True,
            )
        except Exception as e:
            logger.error(f"Failed to register task {task.task_id}: {e}")

    # ── Channel delivery ───────────────────────────────────

    async def _send_to_channel(self, user_id: str, channel: str, text: str) -> bool:
        """Deliver a message to the user's channel. Returns True if sent directly."""
        logger.debug(f"Delivery attempt: user={user_id}, channel={channel}, text={text[:50]}")

        if channel == "telegram":
            link = self.db.get_channel_link(user_id, "telegram")
            if link:
                chat_id = link["metadata"].get("chat_id")
                if chat_id:
                    from gbot.core.channels.telegram import send_message
                    logger.debug(f"Sending to Telegram: chat_id={chat_id}, token={link['channel_user_id'][:10]}...")
                    await send_message(link["channel_user_id"], int(chat_id), text)
                    return True
                logger.warning(f"No chat_id for user {user_id}")
            else:
                logger.warning(f"No telegram link for user {user_id}")
            return False

        if channel == "whatsapp":
            link = self.db.get_channel_link(user_id, "whatsapp")
            if link and self.config:
                from gbot.core.channels.waha_client import WAHAClient
                from gbot.core.channels.whatsapp import send_whatsapp_message
                chat_id = WAHAClient.phone_to_chat_id(link["channel_user_id"])
                await send_whatsapp_message(
                    self.config.channels.whatsapp, chat_id, text
                )
                return True
            logger.warning(f"No whatsapp link for user {user_id}")
            return False

        # API/WS channel: try WebSocket push, fallback to system_event
        ws_manager = getattr(self, "ws_manager", None)
        if ws_manager and ws_manager.is_connected(user_id):
            sent = await ws_manager.send_event(user_id, {
                "type": "event",
                "event_type": "message",
                "source": "cron",
                "payload": text,
            })
            if sent:
                logger.info(f"Event pushed via WS to user={user_id}")
                return True

        # Fallback: save as system_event for polling
        self.db.add_system_event(user_id, "cron", "message", text, channel=channel)
        logger.info(f"Event saved to DB for user={user_id} (no active WS)")
        return False


    # ── Post-process: record proactive messages ────────────

    async def _record_proactive_message(
        self,
        user_id: str,
        channel: str,
        response: str,
        processor: str,
        source_id: str,
    ) -> None:
        """Record a short delivery note to user's active session.

        Only records that a message was delivered, NOT the full content.
        This prevents the agent from repeating the proactive message.
        """
        session = self.db.get_active_session(user_id, channel=channel)
        if not session:
            session = self.db.get_active_session(user_id)
        if session:
            summary = response[:200] if response else "(action executed)"
            self.db.add_message(
                session["session_id"],
                role="system",
                content=f"[{source_id}] Delivered to {channel}: {summary}",
            )
            logger.debug(
                f"Proactive delivery note recorded to session {session['session_id']}"
            )

    # ── Processor execution ────────────────────────────────

    async def _run_by_processor(
        self,
        processor: str,
        plan: dict,
        message: str,
        user_id: str,
        channel: str,
        *,
        agent_prompt: str | None = None,
        agent_tools: str | None = None,
        agent_model: str | None = None,
    ) -> tuple[str | None, bool]:
        """Execute based on processor type. Returns (response_text, should_deliver)."""
        if processor == "static":
            text = plan.get("message") or f"Hatirlatma: {message}"
            return text, True

        if processor == "function":
            tool_name = plan.get("tool_name")
            tool_args = plan.get("tool_args", {})
            if "channel" not in tool_args and channel:
                tool_args["channel"] = channel
            tool = self._registry.get(tool_name)
            if tool:
                if hasattr(tool, "ainvoke"):
                    await tool.ainvoke(tool_args)
                else:
                    tool.invoke(tool_args)
                logger.info(f"Function processor: {tool_name}({tool_args})")
            else:
                logger.warning(f"Function processor: tool '{tool_name}' not found")
            return None, False

        if processor == "runner":
            response, _ = await self.runner.process(
                user_id=user_id, channel=channel, message=message,
            )
            return response, True

        # processor == "agent" (default)
        from gbot.agent.light import LightAgent

        prompt = plan.get("prompt") or agent_prompt
        tools_json = json.dumps(plan.get("tools")) if plan.get("tools") else agent_tools
        model = plan.get("model") or agent_model

        if prompt and channel and channel != "telegram":
            prompt += (
                f"\n\nIMPORTANT: When calling send_message_to_user, "
                f"you MUST set channel='{channel}'."
            )

        if prompt and self.config:
            tools = self._parse_tools(tools_json)
            resolved_model = model or self.config.assistant.model
            agent = LightAgent(
                config=self.config, prompt=prompt,
                tools=tools, model=resolved_model,
            )
            text, _, called = await agent.run_with_meta(message)
            if "send_message_to_user" in called:
                return text, False
            return text, True

        # Legacy fallback: no prompt → full runner
        response, _ = await self.runner.process(
            user_id=user_id, channel=channel, message=message, skip_context=True,
        )
        return response, True

    # ── Unified task execution ─────────────────────────────

    async def _execute_task(self, task: BackgroundTask) -> None:
        """Execute any task (recurring, monitor, or delayed)."""
        is_delayed = task.execution_type == "delayed"
        is_recurring = task.execution_type in ("recurring", "monitor")

        logger.info(
            f"Task trigger: {task.task_id} → user={task.user_id}"
            f" ({task.execution_type}, processor={task.processor})"
        )

        plan = json.loads(task.plan_json) if task.plan_json else {}
        processor = task.processor or plan.get("processor", "agent")
        start = time.time()

        try:
            response, should_deliver = await self._run_by_processor(
                processor, plan, task.message, task.user_id, task.channel,
                agent_prompt=task.agent_prompt,
                agent_tools=task.agent_tools,
                agent_model=task.agent_model,
            )
            duration_ms = int((time.time() - start) * 1000)

            # NOTIFY/SKIP: suppress silent responses (recurring/monitor)
            if response and _should_skip(response):
                logger.debug(f"Task {task.task_id} skipped (SKIP marker)")
                self.db.log_execution(
                    task.task_id, task.user_id, status="skipped",
                    result=response, duration_ms=duration_ms,
                )
                return

            # Log success
            self.db.log_execution(
                task.task_id, task.user_id, status="success",
                result=response or "(no output)", duration_ms=duration_ms,
            )

            # Reset failures for recurring tasks
            if is_recurring:
                self.db.reset_task_failures(task.task_id)

            # Deliver response to user's channel
            if should_deliver and response:
                sent = await self._send_to_channel(
                    task.user_id, task.channel, response,
                )
                if sent:
                    logger.info(f"Task {task.task_id} → sent to {task.channel}")
                elif is_delayed and task.channel not in ("api", "ws"):
                    self.db.update_task(
                        task.task_id, retry_count=(task.retry_count or 0) + 1,
                        last_error="Channel delivery failed",
                    )
                    logger.warning(f"Task {task.task_id} delivery failed")
                else:
                    logger.info(f"Task {task.task_id} completed: {(response or '')[:100]}")
            else:
                logger.info(f"Task {task.task_id} executed (no delivery)")

            # Record delivery note to session (always, even if response is None)
            source = f"{'cron' if is_recurring else 'reminder'}:{task.task_id}"
            await self._record_proactive_message(
                task.user_id, task.channel, response or "", processor, source,
            )

            # Mark delayed one-shot tasks as completed
            if is_delayed and not task.cron_expr:
                self.db.update_task(task.task_id, status="sent")

        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            logger.error(f"Task {task.task_id} failed: {e}")
            self.db.log_execution(
                task.task_id, task.user_id, status="error",
                error=str(e), duration_ms=duration_ms,
            )
            if is_recurring:
                count = self.db.increment_task_failures(task.task_id, str(e))
                if count >= 3:
                    self._pause_task(task.task_id)
            elif is_delayed:
                retry = (task.retry_count or 0) + 1
                new_status = "failed" if retry >= 3 else "pending"
                self.db.update_task(
                    task.task_id, retry_count=retry,
                    last_error=str(e), status=new_status,
                )

    # ── Helpers ─────────────────────────────────────────────

    def _parse_tools(self, agent_tools: str | None) -> list:
        """Parse JSON tool name list into actual tool objects."""
        if not agent_tools or not self._registry:
            return []
        try:
            names = json.loads(agent_tools) if isinstance(agent_tools, str) else agent_tools
        except json.JSONDecodeError:
            return []
        return resolve_tools(self._registry, names)

    def _pause_task(self, task_id: str) -> None:
        """Pause a task after consecutive failures."""
        logger.warning(f"Pausing task {task_id} after 3+ consecutive failures")
        self.db.update_task(task_id, enabled=False, status="paused")
        try:
            self._scheduler.remove_job(task_id)
        except Exception:
            pass

    # Legacy aliases
    _execute_job = _execute_task

    async def _execute_reminder(self, row: dict) -> None:
        """Legacy wrapper — convert row dict to BackgroundTask."""
        fields = dict(row)  # Keep all keys for model_validator
        # Fill defaults from reminder context
        if not fields.get("execution_type"):
            fields["execution_type"] = "recurring" if row.get("cron_expr") else "delayed"
        if not fields.get("processor"):
            fields["processor"] = "static"
        task = BackgroundTask(**{k: v for k, v in fields.items()
                                 if k in BackgroundTask.model_fields or k in ("job_id", "reminder_id")})
        await self._execute_task(task)

    def _register_reminder_from_row(self, row: dict) -> None:
        """Legacy wrapper — convert row dict to BackgroundTask and register."""
        fields = {k: v for k, v in row.items() if k in BackgroundTask.model_fields}
        if not fields.get("task_id"):
            fields["task_id"] = row.get("reminder_id", "")
        if not fields.get("message"):
            fields["message"] = row.get("message", "")
        if not fields.get("execution_type"):
            fields["execution_type"] = "recurring" if row.get("cron_expr") else "delayed"
        task = BackgroundTask(**fields)
        self._register_task(task)

    def _register_job(self, job: BackgroundTask) -> None:
        """Legacy wrapper."""
        self._register_task(job)

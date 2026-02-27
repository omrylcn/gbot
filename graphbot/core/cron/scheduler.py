"""CronScheduler — APScheduler + SQLite bridge for dynamic job scheduling."""

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

from graphbot.agent.tools.registry import build_background_tool_registry, resolve_tools
from graphbot.core.cron.types import CronJob
from graphbot.memory.store import MemoryStore

if TYPE_CHECKING:
    from graphbot.agent.runner import GraphRunner
    from graphbot.core.config.schema import Config


def _should_skip(response: str) -> bool:
    """Check if LLM response should be suppressed (SKIP/NO_NOTIFY marker).

    Used by cron jobs with NOTIFY/SKIP prompting. If the LLM determines
    there is nothing to report, it includes a SKIP marker.
    """
    if not response or not response.strip():
        return True
    markers = {"SKIP", "[SKIP]", "[NO_NOTIFY]"}
    upper = response.strip().upper()
    return any(upper.startswith(m) or upper.endswith(m) for m in markers)


class CronScheduler:
    """Bridge between SQLite cron_jobs and APScheduler.

    Jobs are persisted in SQLite (source of truth) and registered
    with APScheduler for execution. On trigger, calls runner.process().
    Supports both recurring cron jobs and one-shot reminders.
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
        """Load cron jobs and reminders from SQLite and start the scheduler."""
        # Load cron jobs
        jobs = self.db.get_cron_jobs()
        for row in jobs:
            if row.get("enabled", 1):
                job = CronJob(**row)
                if job.cron_expr:
                    self._register_job(job)

        # Load pending reminders from standalone table
        reminders = self.db.get_pending_reminders()
        for row in reminders:
            self._register_reminder_from_row(row)

        self._scheduler.start()
        logger.info(
            f"CronScheduler started with {len(jobs)} jobs, {len(reminders)} reminders"
        )

    async def stop(self) -> None:
        """Shutdown the scheduler gracefully."""
        self._scheduler.shutdown(wait=False)
        logger.info("CronScheduler stopped")

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
        """Create a new cron job (SQLite + APScheduler)."""
        agent_tools_json = json.dumps(agent_tools) if agent_tools else None
        job_id = str(uuid.uuid4())[:8]
        self.db.add_cron_job(
            job_id, user_id, cron_expr, message, channel,
            agent_prompt=agent_prompt,
            agent_tools=agent_tools_json,
            agent_model=agent_model,
            notify_condition=notify_condition,
            processor=processor,
            plan_json=plan_json,
        )
        job = CronJob(
            job_id=job_id,
            user_id=user_id,
            cron_expr=cron_expr,
            message=message,
            channel=channel,
            agent_prompt=agent_prompt,
            agent_tools=agent_tools_json,
            agent_model=agent_model,
            notify_condition=notify_condition,
            processor=processor,
            plan_json=plan_json,
        )
        self._register_job(job)
        logger.info(f"Cron job added: {job_id} ({cron_expr})")
        return job

    def remove_job(self, job_id: str) -> None:
        """Remove a cron job (SQLite + APScheduler)."""
        self.db.remove_cron_job(job_id)
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass  # Job may not be in scheduler
        logger.info(f"Cron job removed: {job_id}")

    def list_jobs(self, user_id: str | None = None) -> list[dict]:
        """List cron jobs from SQLite."""
        return self.db.get_cron_jobs(user_id)

    # ── Reminders (one-shot) ────────────────────────────────

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
        """Create a reminder.

        Parameters
        ----------
        cron_expr : str, optional
            When provided, creates a *recurring* reminder using CronTrigger.
        agent_prompt : str, optional
            If set, LightAgent runs with this prompt on trigger.
        agent_tools : list[str], optional
            Tool names for the LightAgent.
        processor : str
            Processor type: "static", "function", or "agent".
        plan_json : str, optional
            JSON with processor-specific config.
        """
        agent_tools_json = json.dumps(agent_tools) if agent_tools else None
        run_at = (datetime.now() + timedelta(seconds=delay_seconds)).isoformat()
        reminder_id = str(uuid.uuid4())[:8]
        self.db.add_reminder(
            reminder_id, user_id, run_at, message, channel, cron_expr=cron_expr,
            agent_prompt=agent_prompt, agent_tools=agent_tools_json,
            processor=processor, plan_json=plan_json,
        )
        row = {
            "reminder_id": reminder_id,
            "user_id": user_id,
            "message": message,
            "channel": channel,
            "run_at": run_at,
            "cron_expr": cron_expr,
            "agent_prompt": agent_prompt,
            "agent_tools": agent_tools_json,
            "processor": processor,
            "plan_json": plan_json,
        }
        self._register_reminder_from_row(row)
        kind = f"recurring ({cron_expr})" if cron_expr else f"one-shot at {run_at}"
        logger.info(f"Reminder added: {reminder_id} ({kind})")
        return row

    def list_reminders(self, user_id: str | None = None) -> list[dict]:
        """List pending reminders from SQLite."""
        return self.db.get_pending_reminders(user_id)

    def cancel_reminder(self, reminder_id: str) -> bool:
        """Cancel a pending reminder. Returns True if cancelled."""
        result = self.db.cancel_reminder(reminder_id)
        try:
            self._scheduler.remove_job(reminder_id)
        except Exception:
            pass
        return result

    def _register_job(self, job: CronJob) -> None:
        """Register a CronJob with APScheduler."""
        try:
            trigger = CronTrigger.from_crontab(job.cron_expr)
            self._scheduler.add_job(
                self._execute_job,
                trigger=trigger,
                id=job.job_id,
                args=[job],
                replace_existing=True,
            )
        except Exception as e:
            logger.error(f"Failed to register cron job {job.job_id}: {e}")

    def _register_reminder_from_row(self, row: dict) -> None:
        """Register a reminder with APScheduler.

        Uses CronTrigger for recurring reminders (cron_expr set),
        DateTrigger for one-shot reminders.
        """
        reminder_id = row["reminder_id"]
        cron_expr = row.get("cron_expr")

        try:
            if cron_expr:
                # Recurring reminder — fires periodically
                trigger = CronTrigger.from_crontab(cron_expr)
            else:
                # One-shot reminder — fires once at run_at
                run_at = row.get("run_at")
                if not run_at:
                    return
                run_date = datetime.fromisoformat(run_at)
                if run_date < datetime.now():
                    self.db.remove_reminder(reminder_id)
                    return
                trigger = DateTrigger(run_date=run_date)

            self._scheduler.add_job(
                self._execute_reminder,
                trigger=trigger,
                id=reminder_id,
                args=[row],
                replace_existing=True,
            )
        except Exception as e:
            logger.error(f"Failed to register reminder {reminder_id}: {e}")

    # ── Channel delivery ─────────────────────────────────────

    async def _send_to_channel(self, user_id: str, channel: str, text: str) -> bool:
        """Deliver a message to the user's channel. Returns True if sent directly."""
        logger.debug(f"Delivery attempt: user={user_id}, channel={channel}, text={text[:50]}")
        if channel == "telegram":
            link = self.db.get_channel_link(user_id, "telegram")
            if link:
                chat_id = link["metadata"].get("chat_id")
                if chat_id:
                    from graphbot.core.channels.telegram import send_message

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
                from graphbot.core.channels.waha_client import WAHAClient
                from graphbot.core.channels.whatsapp import send_whatsapp_message

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

        # Fallback: save as system_event for polling / context injection
        self.db.add_system_event(user_id, "cron", "message", text)
        logger.info(f"Event saved to DB for user={user_id} (no active WS)")
        return False

    # ── Processor execution ──────────────────────────────────

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
        """Execute based on processor type. Returns (response_text, should_deliver).

        Processor types:
        - static: plain text delivery, no LLM
        - function: direct tool call, no LLM, no delivery
        - runner: full GraphRunner with complete context + all tools (self-reminder)
        - agent: LightAgent with LLM + tools, delivers result

        Falls back to legacy agent_prompt/agent_tools if plan is empty.
        """
        if processor == "static":
            text = plan.get("message") or f"Hatirlatma: {message}"
            return text, True

        if processor == "function":
            tool_name = plan.get("tool_name")
            tool_args = plan.get("tool_args", {})
            # Inject channel from context if tool accepts it
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
            return None, False  # action itself is the goal, no delivery

        if processor == "runner":
            # Self-reminder: full GraphRunner with complete context + all tools.
            # Runs as if the owner typed the message — same session, same channel.
            response, _ = await self.runner.process(
                user_id=user_id, channel=channel, message=message,
            )
            return response, True

        # processor == "agent" (default)
        from graphbot.agent.light import LightAgent

        # New plan_json path
        prompt = plan.get("prompt") or agent_prompt
        tools_json = json.dumps(plan.get("tools")) if plan.get("tools") else agent_tools
        model = plan.get("model") or agent_model

        # Inject channel info into prompt so LightAgent uses the correct channel
        if prompt and channel and channel != "telegram":
            prompt += (
                f"\n\nIMPORTANT: When calling send_message_to_user, "
                f"you MUST set channel='{channel}'."
            )

        if prompt and self.config:
            tools = self._parse_tools(tools_json)
            resolved_model = model or self.config.assistant.model
            agent = LightAgent(
                config=self.config,
                prompt=prompt,
                tools=tools,
                model=resolved_model,
            )
            text, _ = await agent.run(message)
            # Agent handles its own delivery via send_message_to_user
            return text, False

        # Legacy fallback: no prompt → full runner
        response, _ = await self.runner.process(
            user_id=user_id, channel=channel, message=message, skip_context=True,
        )
        return response, True

    # ── Execution ─────────────────────────────────────────────

    async def _execute_job(self, job: CronJob) -> None:
        """Execute a cron job based on its processor type.

        Supports static/function/agent processors via plan_json.
        Falls back to legacy agent_prompt if plan_json is empty.
        Supports NOTIFY/SKIP markers and tracks consecutive failures.
        """
        logger.info(f"Cron trigger: {job.job_id} → user={job.user_id}")
        plan = json.loads(job.plan_json) if job.plan_json else {}
        processor = job.processor or plan.get("processor", "agent")
        start = time.time()
        try:
            response, should_deliver = await self._run_by_processor(
                processor, plan, job.message, job.user_id, job.channel,
                agent_prompt=job.agent_prompt,
                agent_tools=job.agent_tools,
                agent_model=job.agent_model,
            )
            duration_ms = int((time.time() - start) * 1000)

            # NOTIFY/SKIP: suppress silent responses
            if response and _should_skip(response):
                logger.debug(f"Cron {job.job_id} skipped (SKIP marker in response)")
                self.db.log_cron_execution(
                    job.job_id, response, "skipped", duration_ms=duration_ms,
                )
                return

            # Log success + reset failures
            self.db.log_cron_execution(
                job.job_id, response or "(no output)", "success",
                duration_ms=duration_ms,
            )
            self.db.reset_cron_failures(job.job_id)

            # Deliver response to user's channel
            if should_deliver and response:
                sent = await self._send_to_channel(
                    job.user_id, job.channel, response,
                )
                if sent:
                    logger.info(f"Cron job {job.job_id} → sent to {job.channel}")
                else:
                    logger.info(
                        f"Cron job {job.job_id} completed: {response[:100]}"
                    )
            else:
                logger.info(f"Cron job {job.job_id} executed (no delivery)")
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            logger.error(f"Cron job {job.job_id} failed: {e}")
            self.db.log_cron_execution(
                job.job_id, str(e), "error", duration_ms=duration_ms,
            )
            count = self.db.increment_cron_failures(job.job_id, str(e))
            if count >= 3:
                self._pause_job(job.job_id)

    def _parse_tools(self, agent_tools: str | None) -> list:
        """Parse JSON tool name list into actual tool objects.

        Returns empty list if agent_tools is None/empty or registry is empty.
        """
        if not agent_tools or not self._registry:
            return []
        try:
            names = json.loads(agent_tools) if isinstance(agent_tools, str) else agent_tools
        except json.JSONDecodeError:
            return []
        return resolve_tools(self._registry, names)

    def _pause_job(self, job_id: str) -> None:
        """Pause a job after consecutive failures by disabling it."""
        logger.warning(f"Pausing cron job {job_id} after 3+ consecutive failures")
        with self.db._get_conn() as conn:
            conn.execute(
                "UPDATE cron_jobs SET enabled = 0 WHERE job_id = ?", (job_id,),
            )
            conn.commit()
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass

    async def _execute_reminder(self, row: dict) -> None:
        """Execute a reminder based on its processor type.

        Supports static/function/agent processors via plan_json.
        Falls back to legacy agent_prompt if plan_json is empty.
        One-shot reminders are marked 'sent' after delivery.
        Recurring reminders stay 'pending' so they keep firing.
        """
        reminder_id = row["reminder_id"]
        user_id = row["user_id"]
        channel = row.get("channel", "telegram")
        is_recurring = bool(row.get("cron_expr"))
        plan = json.loads(row.get("plan_json") or "{}") if row.get("plan_json") else {}
        processor = row.get("processor") or plan.get("processor", "static")

        logger.info(
            f"Reminder trigger: {reminder_id} → user={user_id}"
            f" ({'recurring' if is_recurring else 'one-shot'},"
            f" processor={processor})"
        )
        try:
            response, should_deliver = await self._run_by_processor(
                processor, plan, row["message"], user_id, channel,
                agent_prompt=row.get("agent_prompt"),
                agent_tools=row.get("agent_tools"),
            )

            if should_deliver and response:
                sent = await self._send_to_channel(user_id, channel, response)
                if sent:
                    logger.info(f"Reminder {reminder_id} sent to {channel}")
                elif channel in ("api", "ws"):
                    logger.info(f"Reminder {reminder_id} saved as system_event")
                else:
                    self.db.mark_reminder_failed(
                        reminder_id, "Channel delivery failed",
                    )
                    logger.warning(f"Reminder {reminder_id} delivery failed")
                    return
            else:
                logger.info(f"Reminder {reminder_id} executed (no delivery)")

            # Mark one-shot reminders as sent
            if not is_recurring:
                self.db.mark_reminder_sent(reminder_id)
        except Exception as e:
            self.db.mark_reminder_failed(reminder_id, str(e))
            logger.error(f"Reminder {reminder_id} failed: {e}")

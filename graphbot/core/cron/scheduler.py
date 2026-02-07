"""CronScheduler — APScheduler + SQLite bridge for dynamic job scheduling."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from loguru import logger

from graphbot.core.cron.types import CronJob
from graphbot.memory.store import MemoryStore

if TYPE_CHECKING:
    from graphbot.agent.runner import GraphRunner
    from graphbot.core.config.schema import Config


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

    async def start(self) -> None:
        """Load jobs and reminders from SQLite and start the scheduler."""
        jobs = self.db.get_cron_jobs()
        for row in jobs:
            if row.get("enabled", 1):
                job = CronJob(**row)
                if job.run_at:
                    self._register_reminder(job)
                elif job.cron_expr:
                    self._register_job(job)
        self._scheduler.start()
        logger.info(f"CronScheduler started with {len(jobs)} jobs")

    async def stop(self) -> None:
        """Shutdown the scheduler gracefully."""
        self._scheduler.shutdown(wait=False)
        logger.info("CronScheduler stopped")

    def add_job(
        self, user_id: str, cron_expr: str, message: str, channel: str = "api"
    ) -> CronJob:
        """Create a new cron job (SQLite + APScheduler)."""
        job_id = str(uuid.uuid4())[:8]
        self.db.add_cron_job(job_id, user_id, cron_expr, message, channel)
        job = CronJob(
            job_id=job_id,
            user_id=user_id,
            cron_expr=cron_expr,
            message=message,
            channel=channel,
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
        self, user_id: str, channel: str, delay_seconds: int, message: str
    ) -> CronJob:
        """Create a one-shot reminder that fires after delay_seconds."""
        run_at = (datetime.now() + timedelta(seconds=delay_seconds)).isoformat()
        job_id = str(uuid.uuid4())[:8]
        self.db.add_reminder(job_id, user_id, run_at, message, channel)
        job = CronJob(
            job_id=job_id,
            user_id=user_id,
            message=message,
            channel=channel,
            run_at=run_at,
        )
        self._register_reminder(job)
        logger.info(f"Reminder added: {job_id} (fires at {run_at})")
        return job

    def list_reminders(self, user_id: str | None = None) -> list[dict]:
        """List pending reminders from SQLite."""
        return self.db.get_pending_reminders(user_id)

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

    def _register_reminder(self, job: CronJob) -> None:
        """Register a one-shot reminder with APScheduler DateTrigger."""
        if not job.run_at:
            return
        try:
            run_date = datetime.fromisoformat(job.run_at)
            # Skip past reminders
            if run_date < datetime.now():
                self.db.remove_cron_job(job.job_id)
                return
            trigger = DateTrigger(run_date=run_date)
            self._scheduler.add_job(
                self._execute_reminder,
                trigger=trigger,
                id=job.job_id,
                args=[job],
                replace_existing=True,
            )
        except Exception as e:
            logger.error(f"Failed to register reminder {job.job_id}: {e}")

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
        else:
            logger.debug(f"Channel '{channel}' has no direct delivery, skipping")
        return False

    # ── Execution ─────────────────────────────────────────────

    async def _execute_job(self, job: CronJob) -> None:
        """Execute a cron job: run through LLM, deliver response to channel."""
        logger.info(f"Cron trigger: {job.job_id} → user={job.user_id}")
        try:
            response, session_id = await self.runner.process(
                user_id=job.user_id,
                channel=job.channel,
                message=job.message,
            )
            # Deliver LLM response to the user's channel
            sent = await self._send_to_channel(job.user_id, job.channel, response)
            if sent:
                logger.info(f"Cron job {job.job_id} → sent to {job.channel}")
            else:
                logger.info(f"Cron job {job.job_id} completed: {response[:100]}")
        except Exception as e:
            logger.error(f"Cron job {job.job_id} failed: {e}")

    async def _execute_reminder(self, job: CronJob) -> None:
        """Execute a one-shot reminder: send message directly, then clean up."""
        logger.info(f"Reminder trigger: {job.job_id} → user={job.user_id}")
        try:
            text = f"Hatirlatma: {job.message}"
            sent = await self._send_to_channel(job.user_id, job.channel, text)
            if not sent:
                # Fallback: process through LLM
                await self.runner.process(
                    job.user_id, job.channel, f"Reminder: {job.message}"
                )
        except Exception as e:
            logger.error(f"Reminder {job.job_id} failed: {e}")
        finally:
            # One-shot: remove from SQLite
            self.db.remove_cron_job(job.job_id)

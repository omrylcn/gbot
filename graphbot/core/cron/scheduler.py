"""CronScheduler — APScheduler + SQLite bridge for dynamic job scheduling."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from graphbot.core.cron.types import CronJob
from graphbot.memory.store import MemoryStore

if TYPE_CHECKING:
    from graphbot.agent.runner import GraphRunner


class CronScheduler:
    """Bridge between SQLite cron_jobs and APScheduler.

    Jobs are persisted in SQLite (source of truth) and registered
    with APScheduler for execution. On trigger, calls runner.process().
    """

    def __init__(self, db: MemoryStore, runner: GraphRunner):
        self.db = db
        self.runner = runner
        self._scheduler = AsyncIOScheduler(
            job_defaults={"coalesce": True, "max_instances": 1}
        )

    async def start(self) -> None:
        """Load jobs from SQLite and start the scheduler."""
        jobs = self.db.get_cron_jobs()
        for row in jobs:
            if row.get("enabled", 1):
                self._register_job(CronJob(**row))
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

    async def _execute_job(self, job: CronJob) -> None:
        """Execute a cron job by calling runner.process()."""
        logger.info(f"Cron trigger: {job.job_id} → user={job.user_id}")
        try:
            response, session_id = await self.runner.process(
                user_id=job.user_id,
                channel=job.channel,
                message=job.message,
            )
            logger.info(f"Cron job {job.job_id} completed: {response[:100]}")
        except Exception as e:
            logger.error(f"Cron job {job.job_id} failed: {e}")

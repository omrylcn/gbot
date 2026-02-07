"""Cron tool — dynamic job scheduling via APScheduler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from graphbot.core.cron.scheduler import CronScheduler


def make_cron_tools(scheduler: CronScheduler | None = None) -> list:
    """Create cron tools. Returns empty list if no scheduler provided."""
    if scheduler is None:
        return []

    @tool
    def add_cron_job(
        user_id: str, cron_expr: str, message: str, channel: str = "api"
    ) -> str:
        """Schedule a recurring task. Uses cron expressions (e.g. '0 9 * * *' = every day at 9am)."""
        try:
            job = scheduler.add_job(user_id, cron_expr, message, channel)
            return f"Cron job created: {job.job_id} ({cron_expr})"
        except Exception as e:
            return f"Failed to create cron job: {e}"

    @tool
    def list_cron_jobs(user_id: str) -> str:
        """List all scheduled cron jobs for a user."""
        jobs = scheduler.list_jobs(user_id)
        if not jobs:
            return "No scheduled jobs."
        lines = []
        for j in jobs:
            status = "enabled" if j.get("enabled", 1) else "disabled"
            lines.append(f"- [{j['job_id']}] {j['cron_expr']} → {j['message'][:50]} ({status})")
        return "\n".join(lines)

    @tool
    def remove_cron_job(job_id: str) -> str:
        """Remove a scheduled cron job by its ID."""
        scheduler.remove_job(job_id)
        return f"Cron job {job_id} removed."

    return [add_cron_job, list_cron_jobs, remove_cron_job]

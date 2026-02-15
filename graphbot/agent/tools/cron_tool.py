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
        user_id: str,
        cron_expr: str,
        message: str,
        channel: str = "api",
        agent_prompt: str | None = None,
        agent_model: str | None = None,
        notify_condition: str = "always",
    ) -> str:
        """Schedule a recurring task.

        Uses cron expressions (e.g. '0 9 * * *' = every day at 9am).
        Optionally provide agent_prompt to use LightAgent (cheaper, isolated).
        Set notify_condition='notify_skip' to suppress SKIP responses.
        """
        try:
            job = scheduler.add_job(
                user_id, cron_expr, message, channel,
                agent_prompt=agent_prompt,
                agent_model=agent_model,
                notify_condition=notify_condition,
            )
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
            lines.append(
                f"- [{j['job_id']}] {j['cron_expr']} → {j['message'][:50]} ({status})"
            )
        return "\n".join(lines)

    @tool
    def remove_cron_job(job_id: str) -> str:
        """Remove a scheduled cron job by its ID."""
        scheduler.remove_job(job_id)
        return f"Cron job {job_id} removed."

    @tool
    def create_alert(
        user_id: str,
        cron_expr: str,
        check_message: str,
        channel: str = "api",
        agent_model: str | None = None,
    ) -> str:
        """Create a monitoring alert that only notifies when something needs attention.

        Uses NOTIFY/SKIP: LLM checks the condition and only sends a message
        if there is something to report. Silent otherwise.
        Example: create_alert('u1', '*/10 * * * *', 'Check gold price, notify if > $2000')
        """
        prompt = (
            "You are a monitoring agent. Check the given condition.\n"
            "If there is something to report, respond with the notification.\n"
            "If everything is normal and nothing to report, respond with: [SKIP]"
        )
        try:
            job = scheduler.add_job(
                user_id, cron_expr, check_message, channel,
                agent_prompt=prompt,
                agent_model=agent_model,
                notify_condition="notify_skip",
            )
            return f"Alert created: {job.job_id} ({cron_expr})"
        except Exception as e:
            return f"Failed to create alert: {e}"

    return [add_cron_job, list_cron_jobs, remove_cron_job, create_alert]

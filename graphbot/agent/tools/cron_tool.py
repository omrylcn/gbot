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
        agent_tools: list[str] | None = None,
        agent_model: str | None = None,
        notify_condition: str = "always",
    ) -> str:
        """Schedule a recurring task using a cron expression.

        Uses cron expressions (e.g. '0 9 * * *' = every day at 9am).

        When the task involves executing an action (e.g. sending a message to
        another user, fetching data), always set agent_prompt and agent_tools:
        - agent_prompt: clear instruction for the background agent
        - agent_tools: list of tool names the agent needs, e.g. ["send_message_to_user"]
        - message: short task description (NOT the user's original sentence)

        Available agent_tools: send_message_to_user, web_search, web_fetch,
        save_memory, search_memory.

        Examples:
        - "Her 10 dakikada Murat'a 'naber' yaz":
            message="Send 'naber' to user Murat"
            agent_prompt="Send the specified message to the target user using send_message_to_user tool."
            agent_tools=["send_message_to_user"]
            cron_expr="*/10 * * * *"
        - "Her sabah 9'da hava durumunu kontrol et":
            message="Check today's weather and report"
            agent_prompt="Fetch current weather and summarize."
            agent_tools=["web_search"]
            cron_expr="0 9 * * *"

        Set notify_condition='notify_skip' to suppress SKIP responses (for alerts).
        """
        try:
            job = scheduler.add_job(
                user_id, cron_expr, message, channel,
                agent_prompt=agent_prompt,
                agent_tools=agent_tools,
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
        agent_tools: list[str] | None = None,
        agent_model: str | None = None,
    ) -> str:
        """Create a monitoring alert that only notifies when a condition is met.

        IMPORTANT: check_message is a TASK INSTRUCTION, not a notification text.
        The background agent will execute check_message as a task. It must
        describe what to check and when to notify vs when to stay silent.

        Wrong: check_message="Gold price exceeded $2000!"  (this is a result)
        Right: check_message="Use web_fetch with 'gold' shortcut to check gold
               prices. If gram gold > 7500 TL, report the price. Otherwise
               respond with [SKIP]."

        The agent will use the provided tools to check the condition on each
        trigger. If there is something to report, user gets notified. If the
        agent responds with [SKIP], the notification is silently suppressed.

        agent_tools: list of tool names the agent needs to check the condition.
        Available: web_search, web_fetch, save_memory, search_memory.
        Default (if None): ["web_search", "web_fetch"]

        Examples:
        - Monitor gold price:
            check_message="Use web_fetch('gold') to check gold prices. If gram
                gold exceeds 7500 TL, report the current price. Otherwise [SKIP]."
            agent_tools=["web_fetch"]
            cron_expr="*/30 * * * *"

        - Monitor earthquake activity:
            check_message="Use web_fetch('earthquake') to check recent earthquakes.
                If there is a quake above 4.0 magnitude in the last hour, report
                details. Otherwise [SKIP]."
            agent_tools=["web_fetch"]
            cron_expr="*/15 * * * *"

        Note: channel is auto-injected from session context, do not set manually.
        """
        prompt = (
            "You are a monitoring agent. Execute the task described below.\n"
            "Use the provided tools to check the condition.\n"
            "If the condition is met and there is something to report, "
            "respond with a clear notification message.\n"
            "If the condition is NOT met and nothing needs attention, "
            "respond ONLY with: [SKIP]"
        )
        # Default to web tools if no specific tools requested
        tools = agent_tools or ["web_search", "web_fetch"]
        try:
            job = scheduler.add_job(
                user_id, cron_expr, check_message, channel,
                agent_prompt=prompt,
                agent_tools=tools,
                agent_model=agent_model,
                notify_condition="notify_skip",
            )
            return f"Alert created: {job.job_id} ({cron_expr})"
        except Exception as e:
            return f"Failed to create alert: {e}"

    return [add_cron_job, list_cron_jobs, remove_cron_job, create_alert]

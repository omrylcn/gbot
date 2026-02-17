"""Reminder tools — one-shot and recurring scheduling for proactive messaging."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from graphbot.core.cron.scheduler import CronScheduler


def make_reminder_tools(scheduler: CronScheduler | None = None) -> list:
    """Create reminder tools. Returns empty list if no scheduler provided."""
    if scheduler is None:
        return []

    @tool
    def create_reminder(
        user_id: str,
        delay_seconds: int,
        message: str,
        channel: str = "telegram",
        agent_prompt: str | None = None,
        agent_tools: list[str] | None = None,
    ) -> str:
        """Create a one-shot reminder that fires after delay_seconds.

        Two modes — decide based on the task:

        Static (remind ME about something):
          agent_prompt=None, message is sent as-is.
          Example: "2 saat sonra toplantı var diye hatırlat"
            → message="Toplantı hatırlatması!", delay_seconds=7200

        Agent (execute an action after delay):
          Set agent_prompt + agent_tools. LightAgent runs the task.
          Example: "5 dakika sonra Murat'a 'naber' diye mesaj at"
            → message="Send 'naber' to user Murat"
            → agent_prompt="Use send_message_to_user tool to send the message."
            → agent_tools=["send_message_to_user"]
            → delay_seconds=300

        Available agent_tools: send_message_to_user, web_search, web_fetch,
        save_memory, search_memory.

        Note: channel is auto-injected from session context, do not set manually.
        """
        try:
            row = scheduler.add_reminder(
                user_id, channel, delay_seconds, message,
                agent_prompt=agent_prompt, agent_tools=agent_tools,
            )
            minutes = delay_seconds // 60
            mode = "agent" if agent_prompt else "static"
            return (
                f"Reminder set ({mode}): '{message}' in {minutes} minutes "
                f"(id: {row['reminder_id']})"
            )
        except Exception as e:
            return f"Failed to create reminder: {e}"

    @tool
    def list_reminders(user_id: str) -> str:
        """List all pending reminders for a user."""
        reminders = scheduler.list_reminders(user_id)
        if not reminders:
            return "No pending reminders."
        lines = []
        for r in reminders:
            kind = f"recurring ({r['cron_expr']})" if r.get("cron_expr") else r["run_at"]
            lines.append(
                f"- [{r['reminder_id']}] {kind} → {r['message'][:50]}"
            )
        return "\n".join(lines)

    @tool
    def cancel_reminder(reminder_id: str) -> str:
        """Cancel a pending reminder by its ID."""
        try:
            cancelled = scheduler.cancel_reminder(reminder_id)
            if cancelled:
                return f"Reminder {reminder_id} cancelled."
            return f"Reminder {reminder_id} not found or already sent."
        except Exception as e:
            return f"Failed to cancel reminder: {e}"

    return [create_reminder, list_reminders, cancel_reminder]

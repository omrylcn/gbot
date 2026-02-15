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
        user_id: str, delay_seconds: int, message: str, channel: str = "telegram"
    ) -> str:
        """Create a one-shot reminder that sends a message after delay_seconds.

        Use this when the user asks to be reminded about something later.
        Examples: "2 saat sonra hatırlat" → delay_seconds=7200

        Note: channel is auto-injected from session context, do not set manually.
        """
        try:
            row = scheduler.add_reminder(user_id, channel, delay_seconds, message)
            minutes = delay_seconds // 60
            return (
                f"Reminder set: '{message}' in {minutes} minutes "
                f"(id: {row['reminder_id']})"
            )
        except Exception as e:
            return f"Failed to create reminder: {e}"

    @tool
    def create_recurring_reminder(
        user_id: str, cron_expr: str, message: str, channel: str = "telegram"
    ) -> str:
        """Create a recurring reminder that sends a message periodically.

        Uses cron expressions — no LLM processing, message is sent as-is.
        Examples:
          "her 5 dakikada hatırlat" → cron_expr="*/5 * * * *"
          "her gün saat 9'da hatırlat" → cron_expr="0 9 * * *"
          "her saat başı hatırlat" → cron_expr="0 * * * *"

        Note: channel is auto-injected from session context, do not set manually.
        """
        try:
            row = scheduler.add_reminder(
                user_id, channel, delay_seconds=0, message=message,
                cron_expr=cron_expr,
            )
            return (
                f"Recurring reminder set: '{message}' ({cron_expr}) "
                f"(id: {row['reminder_id']})"
            )
        except Exception as e:
            return f"Failed to create recurring reminder: {e}"

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

    return [create_reminder, create_recurring_reminder, list_reminders, cancel_reminder]

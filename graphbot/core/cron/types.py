"""Cron job types."""

from __future__ import annotations

from pydantic import BaseModel


class CronJob(BaseModel):
    """Cron job definition — mirrors SQLite cron_jobs table."""

    job_id: str
    user_id: str
    cron_expr: str = ""
    message: str
    channel: str = "api"
    enabled: bool = True
    run_at: str | None = None  # ISO datetime for one-shot reminders

    agent_prompt: str | None = None   # Custom prompt; None = legacy full runner
    agent_tools: str | None = None    # JSON list of tool names; None = all tools
    agent_model: str | None = None    # Override model; None = config default
    notify_condition: str = "always"  # 'always' | 'notify_skip'
    consecutive_failures: int = 0
    last_error: str | None = None

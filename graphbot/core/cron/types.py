"""Cron job types."""

from __future__ import annotations

from pydantic import BaseModel


class CronJob(BaseModel):
    """Cron job definition — mirrors SQLite cron_jobs table."""

    job_id: str
    user_id: str
    cron_expr: str
    message: str
    channel: str = "api"
    enabled: bool = True

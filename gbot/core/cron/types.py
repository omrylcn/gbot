"""Background task types."""

from __future__ import annotations

from pydantic import BaseModel, model_validator


class BackgroundTask(BaseModel):
    """Unified task definition — mirrors background_tasks table."""

    task_id: str = ""

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_ids(cls, data):
        """Accept job_id or reminder_id as alias for task_id."""
        if isinstance(data, dict):
            if not data.get("task_id"):
                data["task_id"] = data.get("job_id") or data.get("reminder_id") or ""
        return data
    user_id: str
    execution_type: str = "immediate"   # immediate | delayed | recurring | monitor
    processor: str = "agent"            # static | function | agent | runner
    message: str
    channel: str = "api"

    # Scheduling
    cron_expr: str | None = None        # recurring/monitor
    run_at: str | None = None           # delayed one-shot
    enabled: bool = True                # recurring: pause/resume

    # Agent config
    agent_prompt: str | None = None
    agent_tools: str | None = None      # JSON list of tool names
    agent_model: str | None = None
    notify_condition: str = "always"    # always | notify_skip
    plan_json: str | None = None

    # State
    status: str = "pending"
    consecutive_failures: int = 0
    last_error: str | None = None

    @property
    def job_id(self) -> str:
        """Backward compatibility alias for task_id."""
        return self.task_id

    @property
    def reminder_id(self) -> str:
        """Backward compatibility alias for task_id."""
        return self.task_id


# Backward compatibility alias
CronJob = BackgroundTask

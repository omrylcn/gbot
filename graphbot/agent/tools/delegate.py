"""Delegate tools — unified entry point for background/scheduled tasks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from langchain_core.tools import tool
from loguru import logger

if TYPE_CHECKING:
    from graphbot.agent.delegation import DelegationPlanner
    from graphbot.core.background.worker import SubagentWorker
    from graphbot.core.cron.scheduler import CronScheduler
    from graphbot.memory.store import MemoryStore


def make_delegate_tools(
    worker: SubagentWorker | None = None,
    scheduler: CronScheduler | None = None,
    planner: DelegationPlanner | None = None,
    db: MemoryStore | None = None,
) -> list:
    """Create delegation tools.

    Returns up to 3 tools: delegate, list_scheduled_tasks, cancel_scheduled_task.
    Returns empty list if neither worker nor scheduler is provided.
    """
    if worker is None and scheduler is None:
        return []

    @tool
    async def delegate(user_id: str, task: str, channel: str = "api") -> str:
        """Delegate a task for background, delayed, or scheduled execution.

        The planner automatically decides the execution strategy (immediate,
        delayed, recurring, or monitor) and processor type (static message,
        direct function call, or agent with tools).

        Just describe the task clearly, including any timing information.

        Examples:
        - "Research bitcoin price trends" -> immediate background agent
        - "Remind me about the meeting in 2 hours" -> delayed static message
        - "Send hello to Murat in 5 minutes" -> delayed function call
        - "Check weather every morning at 9am" -> recurring agent
        - "Alert me when gold exceeds $3000" -> monitor agent

        Parameters
        ----------
        user_id : str
            User who requested the task.
        task : str
            Task description including any timing details.
        channel : str
            Delivery channel for results.
        """
        try:
            if planner:
                logger.debug(f"Planner invoked for: {task[:80]}")
                plan = await planner.plan(task)
                logger.info(
                    f"Planner result: exec={plan['execution']}, "
                    f"proc={plan['processor']}, "
                    f"tools={plan.get('tools')}, "
                    f"delay={plan.get('delay_seconds')}, "
                    f"cron={plan.get('cron_expr')}, "
                    f"tool_name={plan.get('tool_name')}, "
                    f"model={plan.get('model')}"
                )
            else:
                plan = {
                    "execution": "immediate", "processor": "agent",
                    "tools": ["web_search", "web_fetch","send_message_to_user"],
                    "prompt": "Complete the given task thoroughly.",
                    "model": None,
                }

            execution = plan["execution"]
            processor = plan["processor"]

            # Guard: runner processor only allowed with delayed/recurring
            if processor == "runner" and execution == "immediate":
                processor = "agent"
                plan["processor"] = "agent"
                logger.warning(
                    "runner processor downgraded to agent for immediate execution"
                )

            plan_json_str = json.dumps(plan)

            # Route based on execution type
            if execution == "immediate":
                if worker is None:
                    return "Background worker not available."
                task_id = worker.spawn(
                    user_id, task, channel,
                    tools=plan.get("tools"),
                    prompt=plan.get("prompt"),
                    model=plan.get("model"),
                )
                ref_id = f"bg:{task_id}"

            elif execution == "delayed":
                if scheduler is None:
                    return "Scheduler not available."
                delay = plan.get("delay_seconds") or 60
                result = scheduler.add_reminder(
                    user_id, channel, int(delay), task,
                    processor=processor, plan_json=plan_json_str,
                )
                ref_id = f"reminder:{result['reminder_id']}"

            elif execution in ("recurring", "monitor"):
                if scheduler is None:
                    return "Scheduler not available."
                cron_expr = plan.get("cron_expr") or "0 * * * *"
                notify = "notify_skip" if execution == "monitor" else "always"
                job = scheduler.add_job(
                    user_id, cron_expr, task, channel,
                    notify_condition=notify,
                    processor=processor, plan_json=plan_json_str,
                )
                ref_id = f"cron:{job.job_id}"

            else:
                return f"Unknown execution type: {execution}"

            # Log delegation decision
            if db:
                db.log_delegation(
                    user_id, task, execution, processor,
                    reference_id=ref_id, plan_json=plan_json_str,
                )

            logger.info(
                f"Delegated: {ref_id} (exec={execution}, proc={processor})"
            )
            return (
                f"OK — task delegated ({ref_id}, {execution}/{processor}). "
                f"The result will be delivered to the user's channel automatically. "
                f"STOP — do NOT call delegate again. Just confirm to the user."
            )
        except Exception as e:
            logger.error(f"Delegation failed: {e}")
            return f"Failed to delegate task: {e}"

    @tool
    def list_scheduled_tasks(user_id: str) -> str:
        """List all scheduled and pending tasks for a user.

        Shows cron jobs, pending reminders, and running background tasks.
        """
        lines: list[str] = []

        if scheduler:
            # Cron jobs
            jobs = scheduler.list_jobs(user_id)
            for j in jobs:
                status = "enabled" if j.get("enabled", 1) else "disabled"
                proc = j.get("processor", "agent")
                lines.append(
                    f"- [cron:{j['job_id']}] {j['cron_expr']} → "
                    f"{j['message'][:50]} ({status}, {proc})"
                )

            # Reminders
            reminders = scheduler.list_reminders(user_id)
            for r in reminders:
                proc = r.get("processor", "static")
                lines.append(
                    f"- [reminder:{r['reminder_id']}] at {r['run_at']} → "
                    f"{r['message'][:50]} ({proc})"
                )

        if not lines:
            return "No scheduled tasks."
        return "\n".join(lines)

    @tool
    def cancel_scheduled_task(task_id: str) -> str:
        """Cancel a scheduled task by its ID.

        Use the full ID from list_scheduled_tasks output:
        - "cron:abc123" for cron jobs
        - "reminder:def456" for reminders
        Or just the raw ID — both cron and reminder will be tried.
        """
        if scheduler is None:
            return "Scheduler not available."

        if task_id.startswith("cron:"):
            raw_id = task_id[5:]
            scheduler.remove_job(raw_id)
            return f"Cron job {raw_id} removed."

        if task_id.startswith("reminder:"):
            raw_id = task_id[9:]
            ok = scheduler.cancel_reminder(raw_id)
            return f"Reminder {raw_id} cancelled." if ok else f"Reminder {raw_id} not found or already sent."

        # Raw ID — try both
        try:
            scheduler.remove_job(task_id)
            return f"Cron job {task_id} removed."
        except Exception:
            pass
        ok = scheduler.cancel_reminder(task_id)
        if ok:
            return f"Reminder {task_id} cancelled."
        return f"Task {task_id} not found."

    return [delegate, list_scheduled_tasks, cancel_scheduled_task]

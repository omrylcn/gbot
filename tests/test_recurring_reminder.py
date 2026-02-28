"""Tests for Recurring Reminder — periodic, no LLM, direct message delivery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphbot.agent.tools.reminder import make_reminder_tools
from graphbot.api.ws import ConnectionManager
from graphbot.core.cron.scheduler import CronScheduler
from graphbot.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(str(tmp_path / "test.db"))
    s.get_or_create_user("u1", "Test User")
    return s


@pytest.fixture
def mock_runner():
    runner = AsyncMock()
    runner.process = AsyncMock(return_value=("Done", "sess-1"))
    return runner


@pytest.fixture
def manager():
    return ConnectionManager()


# ── Store: cron_expr column ─────────────────────────────────


def test_store_add_recurring_reminder(store):
    """Recurring reminder gets cron_expr persisted."""
    store.add_reminder(
        "r1", "u1", "2026-01-01T00:00:00", "Wake up!", "ws",
        cron_expr="*/5 * * * *",
    )
    reminders = store.get_pending_reminders("u1")
    assert len(reminders) == 1
    assert reminders[0]["cron_expr"] == "*/5 * * * *"
    assert reminders[0]["reminder_id"] == "r1"


def test_store_oneshot_no_cron_expr(store):
    """One-shot reminder has cron_expr=None."""
    store.add_reminder("r1", "u1", "2026-12-31T10:00:00", "Test", "telegram")
    reminders = store.get_pending_reminders("u1")
    assert reminders[0]["cron_expr"] is None


# ── Scheduler: add_reminder with cron_expr ───────────────────


@pytest.mark.asyncio
async def test_scheduler_add_recurring_reminder(store, mock_runner):
    sched = CronScheduler(store, mock_runner)

    with patch.object(sched, "_scheduler") as mock_apscheduler:
        row = sched.add_reminder(
            "u1", "ws", delay_seconds=0, message="Periodic hello",
            cron_expr="*/5 * * * *",
        )

    assert row["reminder_id"]
    assert row["cron_expr"] == "*/5 * * * *"
    assert row["message"] == "Periodic hello"

    # Verify it was registered with APScheduler
    mock_apscheduler.add_job.assert_called_once()


@pytest.mark.asyncio
async def test_scheduler_register_recurring_uses_cron_trigger(store, mock_runner):
    """Recurring reminder should use CronTrigger, not DateTrigger."""
    sched = CronScheduler(store, mock_runner)
    row = {
        "reminder_id": "r1",
        "user_id": "u1",
        "message": "Hello",
        "channel": "ws",
        "run_at": "2026-01-01T00:00:00",
        "cron_expr": "*/5 * * * *",
    }
    with patch.object(sched, "_scheduler") as mock_apscheduler:
        sched._register_reminder_from_row(row)

    call_kwargs = mock_apscheduler.add_job.call_args
    trigger = call_kwargs.kwargs.get("trigger") or call_kwargs[1].get("trigger")
    from apscheduler.triggers.cron import CronTrigger

    assert isinstance(trigger, CronTrigger)


@pytest.mark.asyncio
async def test_scheduler_register_oneshot_uses_date_trigger(store, mock_runner):
    """One-shot reminder should still use DateTrigger."""
    sched = CronScheduler(store, mock_runner)
    row = {
        "reminder_id": "r2",
        "user_id": "u1",
        "message": "Once",
        "channel": "ws",
        "run_at": "2099-01-01T00:00:00",
        "cron_expr": None,
    }
    with patch.object(sched, "_scheduler") as mock_apscheduler:
        sched._register_reminder_from_row(row)

    call_kwargs = mock_apscheduler.add_job.call_args
    trigger = call_kwargs.kwargs.get("trigger") or call_kwargs[1].get("trigger")
    from apscheduler.triggers.date import DateTrigger

    assert isinstance(trigger, DateTrigger)


# ── Execute: recurring stays pending ────────────────────────


@pytest.mark.asyncio
async def test_recurring_not_marked_sent(store, mock_runner, manager):
    """Recurring reminder should NOT be marked as 'sent' after delivery."""
    store.add_reminder(
        "r1", "u1", "2026-01-01T00:00:00", "Recurring msg", "ws",
        cron_expr="*/5 * * * *",
    )
    sched = CronScheduler(store, mock_runner)

    ws = MagicMock()
    ws.send_json = AsyncMock()
    manager.connect("u1", ws)
    sched.ws_manager = manager

    row = {
        "reminder_id": "r1",
        "user_id": "u1",
        "message": "Recurring msg",
        "channel": "ws",
        "cron_expr": "*/5 * * * *",
    }
    await sched._execute_reminder(row)

    # Still pending — not marked as sent
    reminders = store.get_pending_reminders("u1")
    assert len(reminders) == 1
    assert reminders[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_oneshot_marked_sent(store, mock_runner, manager):
    """One-shot reminder should be marked as 'sent' after delivery."""
    store.add_reminder("r2", "u1", "2026-12-31T10:00:00", "Once msg", "ws")
    sched = CronScheduler(store, mock_runner)

    ws = MagicMock()
    ws.send_json = AsyncMock()
    manager.connect("u1", ws)
    sched.ws_manager = manager

    row = {
        "reminder_id": "r2",
        "user_id": "u1",
        "message": "Once msg",
        "channel": "ws",
        "run_at": "2026-12-31T10:00:00",
        "cron_expr": None,
    }
    await sched._execute_reminder(row)

    # Should be marked as sent
    reminders = store.get_pending_reminders("u1")
    assert len(reminders) == 0


# ── WS delivery for recurring ──────────────────────────────


@pytest.mark.asyncio
async def test_recurring_ws_push(store, mock_runner, manager):
    """Recurring reminder pushes via WS when connected."""
    sched = CronScheduler(store, mock_runner)

    ws = MagicMock()
    ws.send_json = AsyncMock()
    manager.connect("u1", ws)
    sched.ws_manager = manager

    row = {
        "reminder_id": "r1",
        "user_id": "u1",
        "message": "Periodic ping",
        "channel": "ws",
        "cron_expr": "*/5 * * * *",
    }
    await sched._execute_reminder(row)

    ws.send_json.assert_called_once()
    payload = ws.send_json.call_args[0][0]
    assert payload["type"] == "event"
    assert "Periodic ping" in payload["payload"]


@pytest.mark.asyncio
async def test_recurring_db_fallback(store, mock_runner):
    """Recurring reminder falls back to DB when no WS connection."""
    sched = CronScheduler(store, mock_runner)
    # No ws_manager — fallback to DB

    row = {
        "reminder_id": "r1",
        "user_id": "u1",
        "message": "Offline ping",
        "channel": "ws",
        "cron_expr": "*/5 * * * *",
    }
    await sched._execute_reminder(row)

    # Event should be saved to system_events
    events = store.get_undelivered_events("u1")
    assert len(events) >= 1
    assert "Offline ping" in events[0]["payload"]


# ── Tool: create_reminder (agent mode) ─────────────────────


def test_create_reminder_agent_mode(store, mock_runner):
    """create_reminder with agent_prompt creates an agent-mode reminder."""
    sched = CronScheduler(store, mock_runner)
    with patch.object(sched, "_scheduler"):
        tools = make_reminder_tools(sched)
        create_rem = next(t for t in tools if t.name == "create_reminder")
        result = create_rem.invoke({
            "user_id": "u1",
            "delay_seconds": 300,
            "message": "Send hello to Murat",
            "channel": "ws",
            "agent_prompt": "Use send_message_to_user to deliver the message.",
            "agent_tools": ["send_message_to_user"],
        })
    assert "agent" in result
    assert "id:" in result

    reminders = store.get_pending_reminders("u1")
    assert len(reminders) == 1
    assert reminders[0]["agent_prompt"] is not None


def test_list_reminders_shows_recurring(store, mock_runner):
    sched = CronScheduler(store, mock_runner)
    store.add_reminder(
        "r1", "u1", "2026-01-01T00:00:00", "Recurring", "ws",
        cron_expr="0 9 * * *",
    )
    store.add_reminder("r2", "u1", "2026-12-31T10:00:00", "One-shot", "ws")

    tools = make_reminder_tools(sched)
    list_tool = next(t for t in tools if t.name == "list_reminders")
    result = list_tool.invoke({"user_id": "u1"})

    assert "recurring (0 9 * * *)" in result
    assert "One-shot" in result

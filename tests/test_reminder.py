"""Tests for Proactive Messaging — Reminder / Alarm."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from graphbot.agent.tools.reminder import make_reminder_tools
from graphbot.core.config import Config
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
def cfg():
    return Config(channels={"telegram": {"enabled": True}})


# ── Store: channel metadata ────────────────────────────────


def test_store_channel_metadata(store):
    store.link_channel("u1", "telegram", "12345")
    store.update_channel_metadata("telegram", "12345", {"chat_id": 99999})

    meta = store.get_channel_metadata("u1", "telegram")
    assert meta["chat_id"] == 99999


# ── Store: reminders ───────────────────────────────────────


def test_store_add_reminder(store):
    store.add_reminder("r1", "u1", "2026-12-31T10:00:00", "Wake up!", "telegram")
    jobs = store.get_cron_jobs("u1")
    assert len(jobs) == 1
    assert jobs[0]["run_at"] == "2026-12-31T10:00:00"
    assert jobs[0]["cron_expr"] == ""


def test_store_pending_reminders(store):
    store.add_reminder("r1", "u1", "2026-12-31T10:00:00", "Reminder 1", "telegram")
    store.add_cron_job("c1", "u1", "0 9 * * *", "Cron job", "api")

    reminders = store.get_pending_reminders("u1")
    assert len(reminders) == 1
    assert reminders[0]["job_id"] == "r1"


# ── Scheduler: add_reminder ────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_add_reminder(store, mock_runner, cfg):
    sched = CronScheduler(store, mock_runner, config=cfg)

    with patch.object(sched, "_scheduler"):
        job = sched.add_reminder("u1", "telegram", 3600, "Test reminder")

    assert job.job_id
    assert job.run_at is not None
    assert job.message == "Test reminder"

    reminders = sched.list_reminders("u1")
    assert len(reminders) == 1


# ── Scheduler: execute_reminder ────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_execute_reminder(store, mock_runner, cfg):
    """_execute_reminder sends message via Telegram using token from DB."""
    store.link_channel("u1", "telegram", "fake-token")
    store.update_channel_metadata_by_user("u1", "telegram", {"chat_id": 99999})

    sched = CronScheduler(store, mock_runner, config=cfg)
    store.add_reminder("r1", "u1", "2026-12-31T10:00:00", "Time to wake up", "telegram")

    from graphbot.core.cron.types import CronJob

    job = CronJob(
        job_id="r1", user_id="u1", message="Time to wake up",
        channel="telegram", run_at="2026-12-31T10:00:00",
    )

    with patch(
        "graphbot.core.channels.telegram.send_message", new_callable=AsyncMock
    ) as mock_send:
        await sched._execute_reminder(job)
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][0] == "fake-token"
        assert call_args[0][1] == 99999
        assert "Time to wake up" in call_args[0][2]

    # Job should be cleaned up from SQLite
    assert len(store.get_pending_reminders("u1")) == 0


# ── Reminder tools ─────────────────────────────────────────


def test_reminder_tool_create(store, mock_runner, cfg):
    sched = CronScheduler(store, mock_runner, config=cfg)
    with patch.object(sched, "_scheduler"):
        tools = make_reminder_tools(sched)
        create = next(t for t in tools if t.name == "create_reminder")
        # Simulate channel injection (normally done by execute_tools node)
        result = create.invoke({
            "user_id": "u1", "delay_seconds": 600, "message": "Check oven",
            "channel": "telegram",
        })
    assert "Reminder set" in result
    reminders = store.get_pending_reminders("u1")
    assert reminders
    assert reminders[0]["channel"] == "telegram"


def test_reminder_tool_list(store, mock_runner, cfg):
    sched = CronScheduler(store, mock_runner, config=cfg)
    store.add_reminder("r1", "u1", "2026-12-31T10:00:00", "Test", "telegram")

    tools = make_reminder_tools(sched)
    list_tool = next(t for t in tools if t.name == "list_reminders")
    result = list_tool.invoke({"user_id": "u1"})
    assert "r1" in result
    assert "Test" in result


def test_reminder_tool_none_scheduler():
    tools = make_reminder_tools(scheduler=None)
    assert tools == []

"""Tests for Faz 5 — Background Services (cron, heartbeat, worker)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphbot.core.config import Config
from graphbot.core.cron.types import CronJob
from graphbot.core.cron.scheduler import CronScheduler
from graphbot.core.background.heartbeat import HeartbeatService, _is_empty_content
from graphbot.core.background.worker import SubagentWorker
from graphbot.agent.tools.cron_tool import make_cron_tools
from graphbot.agent.tools.delegate import make_delegate_tools
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
def cfg(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return Config(
        assistant={"workspace": str(ws)},
        background={"heartbeat": {"enabled": True, "interval_s": 1}},
    )


# ── CronScheduler ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_cron_scheduler_add_remove(store, mock_runner):
    """Add a job, list it, remove it."""
    sched = CronScheduler(store, mock_runner)

    with patch.object(sched, "_scheduler"):
        job = sched.add_job("u1", "0 9 * * *", "Good morning", "api")
        assert job.job_id
        assert job.cron_expr == "0 9 * * *"

        jobs = sched.list_jobs("u1")
        assert len(jobs) == 1
        assert jobs[0]["message"] == "Good morning"

        sched.remove_job(job.job_id)
        jobs = sched.list_jobs("u1")
        assert len(jobs) == 0


@pytest.mark.asyncio
async def test_cron_scheduler_start_loads_jobs(store, mock_runner):
    """start() loads existing jobs from SQLite."""
    store.add_cron_job("j1", "u1", "*/5 * * * *", "check status", "api")
    sched = CronScheduler(store, mock_runner)

    with patch.object(sched, "_scheduler") as mock_aps:
        mock_aps.start = MagicMock()
        await sched.start()
        # APScheduler.add_job should have been called for the loaded job
        assert mock_aps.add_job.called


@pytest.mark.asyncio
async def test_cron_execute_job(store, mock_runner):
    """_execute_job calls runner.process with job params."""
    sched = CronScheduler(store, mock_runner)
    job = CronJob(job_id="j1", user_id="u1", cron_expr="0 9 * * *", message="hi")

    await sched._execute_job(job)

    mock_runner.process.assert_called_once_with(
        user_id="u1", channel="api", message="hi"
    )


# ── HeartbeatService ────────────────────────────────────────


def test_is_empty_content():
    """Empty/comment-only HEARTBEAT.md → True."""
    assert _is_empty_content("") is True
    assert _is_empty_content("# Title\n\n<!-- comment -->") is True
    assert _is_empty_content("# Title\n\nDo something") is False


@pytest.mark.asyncio
async def test_heartbeat_skip_empty(cfg, mock_runner):
    """No HEARTBEAT.md → skip, no runner call."""
    hb = HeartbeatService(cfg, mock_runner)
    await hb._tick()
    mock_runner.process.assert_not_called()


@pytest.mark.asyncio
async def test_heartbeat_triggers(cfg, mock_runner):
    """HEARTBEAT.md with content → triggers runner.process."""
    hb_file = Path(cfg.assistant.workspace) / "HEARTBEAT.md"
    hb_file.write_text("# Tasks\n\n- Check server status\n")

    hb = HeartbeatService(cfg, mock_runner)
    await hb._tick()

    mock_runner.process.assert_called_once()
    call_kwargs = mock_runner.process.call_args
    assert call_kwargs.kwargs["user_id"] == "system"
    assert call_kwargs.kwargs["channel"] == "heartbeat"


# ── SubagentWorker ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_spawn(mock_runner):
    """spawn() creates a background task that calls runner.process."""
    worker = SubagentWorker(mock_runner)

    task_id = worker.spawn("u1", "do something", "api")
    assert task_id
    assert worker.get_running_count() >= 0  # may have already completed

    # Wait for the task to finish
    await asyncio.sleep(0.1)
    mock_runner.process.assert_called_once_with(
        user_id="u1", channel="api", message="do something"
    )


@pytest.mark.asyncio
async def test_worker_shutdown(mock_runner):
    """shutdown() waits for all tasks."""
    worker = SubagentWorker(mock_runner)
    worker.spawn("u1", "task 1", "api")
    worker.spawn("u1", "task 2", "api")

    await worker.shutdown()
    assert worker.get_running_count() == 0
    assert mock_runner.process.call_count == 2


# ── Cron Tool Integration ──────────────────────────────────


def test_cron_tools_none():
    """No scheduler → empty list."""
    assert make_cron_tools(None) == []


def test_cron_tools_created(store, mock_runner):
    """With scheduler → 3 tools."""
    sched = CronScheduler(store, mock_runner)
    tools = make_cron_tools(sched)
    assert len(tools) == 3
    names = {t.name for t in tools}
    assert names == {"add_cron_job", "list_cron_jobs", "remove_cron_job"}


def test_cron_tool_add_job(store, mock_runner):
    """add_cron_job tool → scheduler.add_job → result string."""
    sched = CronScheduler(store, mock_runner)
    with patch.object(sched, "_scheduler"):
        tools = make_cron_tools(sched)
        add = next(t for t in tools if t.name == "add_cron_job")
        result = add.invoke({
            "user_id": "u1",
            "cron_expr": "0 9 * * *",
            "message": "Good morning",
        })
        assert "created" in result.lower()


# ── Delegate Tool Integration ───────────────────────────────


def test_delegate_tools_none():
    """No worker → empty list."""
    assert make_delegate_tools(None) == []


def test_delegate_tools_created(mock_runner):
    """With worker → 1 tool."""
    worker = SubagentWorker(mock_runner)
    tools = make_delegate_tools(worker)
    assert len(tools) == 1
    assert tools[0].name == "delegate"

"""Tests for Faz 13 — LightAgent, NOTIFY/SKIP, system_events, background tasks."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from graphbot.core.config import Config
from graphbot.core.cron.scheduler import CronScheduler, _should_skip
from graphbot.core.cron.types import CronJob
from graphbot.core.background.worker import SubagentWorker
from graphbot.memory.store import MemoryStore
from graphbot.agent.context import ContextBuilder


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
    return Config(assistant={"workspace": str(ws)})


# ── Katman 1: NOTIFY/SKIP ─────────────────────────────────


def test_should_skip_markers():
    """SKIP, [SKIP], [NO_NOTIFY] markers → True."""
    assert _should_skip("SKIP") is True
    assert _should_skip("[SKIP]") is True
    assert _should_skip("[NO_NOTIFY]") is True
    assert _should_skip("  [SKIP]  ") is True
    assert _should_skip("Gold price is $2000 [SKIP]") is True
    assert _should_skip("") is True
    assert _should_skip("Gold price alert: above $2000!") is False
    assert _should_skip("Everything looks normal.") is False


@pytest.mark.asyncio
async def test_execute_job_skip(store, mock_runner):
    """SKIP response → not delivered to channel."""
    mock_runner.process = AsyncMock(return_value=("[SKIP]", "sess-1"))
    sched = CronScheduler(store, mock_runner)
    job = CronJob(job_id="j1", user_id="u1", cron_expr="0 9 * * *", message="check")

    with patch.object(sched, "_send_to_channel", new_callable=AsyncMock) as mock_send:
        await sched._execute_job(job)
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_execute_job_notify(store, mock_runner):
    """Normal response → delivered to channel."""
    mock_runner.process = AsyncMock(return_value=("Alert: price up!", "sess-1"))
    sched = CronScheduler(store, mock_runner)
    job = CronJob(job_id="j1", user_id="u1", cron_expr="0 9 * * *", message="check")

    with patch.object(sched, "_send_to_channel", new_callable=AsyncMock) as mock_send:
        await sched._execute_job(job)
        mock_send.assert_called_once()


# ── Katman 2: Execution log + failure tracking ────────────


@pytest.mark.asyncio
async def test_cron_execution_log(store, mock_runner):
    """Job runs → execution log created."""
    store.add_cron_job("j1", "u1", "0 9 * * *", "check", "api")
    sched = CronScheduler(store, mock_runner)
    job = CronJob(job_id="j1", user_id="u1", cron_expr="0 9 * * *", message="check")

    await sched._execute_job(job)

    log = store.get_cron_execution_log("j1")
    assert len(log) == 1
    assert log[0]["status"] == "success"
    assert log[0]["result"] == "Done"


@pytest.mark.asyncio
async def test_consecutive_failures_pause(store, mock_runner):
    """3 consecutive failures → job paused."""
    store.add_cron_job("j1", "u1", "0 9 * * *", "check", "api")
    mock_runner.process = AsyncMock(side_effect=Exception("API error"))
    sched = CronScheduler(store, mock_runner)
    job = CronJob(job_id="j1", user_id="u1", cron_expr="0 9 * * *", message="check")

    with patch.object(sched, "_scheduler"):
        for _ in range(3):
            await sched._execute_job(job)

    jobs = store.get_cron_jobs("u1")
    assert jobs[0]["enabled"] == 0  # paused


# ── Katman 2: System events ───────────────────────────────


def test_system_events_crud(store):
    """Create event → get undelivered → mark delivered."""
    eid = store.add_system_event("u1", "cron:j1", "alert", "Gold > $2000")
    assert eid > 0

    events = store.get_undelivered_events("u1")
    assert len(events) == 1
    assert events[0]["source"] == "cron:j1"
    assert events[0]["payload"] == "Gold > $2000"

    store.mark_events_delivered([events[0]["id"]])
    assert len(store.get_undelivered_events("u1")) == 0


def test_system_events_in_context(store, cfg):
    """Undelivered events appear in context prompt."""
    store.add_system_event("u1", "task:abc", "task_completed", "Research done")

    builder = ContextBuilder(cfg, store)
    prompt = builder.build("u1")
    assert "Background Notifications" in prompt
    assert "Research done" in prompt

    # After build, events should be marked delivered
    assert len(store.get_undelivered_events("u1")) == 0


# ── Katman 2: Reminder CRUD ───────────────────────────────


def test_reminder_crud(store):
    """Full reminder lifecycle: add → pending → sent."""
    store.add_reminder("r1", "u1", "2026-12-31T10:00:00", "Wake up", "telegram")
    pending = store.get_pending_reminders("u1")
    assert len(pending) == 1

    store.mark_reminder_sent("r1")
    assert len(store.get_pending_reminders("u1")) == 0


# ── Katman 3: LightAgent ──────────────────────────────────


@pytest.mark.asyncio
async def test_light_agent_run(cfg):
    """LightAgent runs with mock LLM and returns response."""
    from graphbot.agent.light import LightAgent
    from langchain_core.messages import AIMessage

    mock_response = AIMessage(content="Gold is $1950, all normal. [SKIP]")

    with patch("graphbot.core.providers.litellm.achat", return_value=mock_response):
        with patch("graphbot.core.providers.litellm.setup_provider"):
            agent = LightAgent(config=cfg, prompt="You are a monitor.")
            response, tokens = await agent.run("Check gold price")

    assert "[SKIP]" in response


@pytest.mark.asyncio
async def test_light_agent_isolated(cfg):
    """LightAgent uses its own tools, not the main agent's."""
    from graphbot.agent.light import LightAgent
    from langchain_core.messages import AIMessage

    mock_response = AIMessage(content="Done")

    with patch("graphbot.core.providers.litellm.achat", return_value=mock_response):
        with patch("graphbot.core.providers.litellm.setup_provider"):
            agent = LightAgent(config=cfg, prompt="Test", tools=[])
            assert agent.tools == []
            response, _ = await agent.run("test")
            assert response == "Done"


@pytest.mark.asyncio
async def test_cron_job_with_agent_config(store, mock_runner, cfg):
    """agent_prompt set → LightAgent is used (not runner)."""
    from langchain_core.messages import AIMessage

    mock_response = AIMessage(content="Alert: gold high!")

    sched = CronScheduler(store, mock_runner, config=cfg)
    job = CronJob(
        job_id="j1", user_id="u1", cron_expr="0 9 * * *",
        message="check gold", agent_prompt="You are a monitor.",
    )

    with patch("graphbot.core.providers.litellm.achat", return_value=mock_response):
        with patch("graphbot.core.providers.litellm.setup_provider"):
            with patch.object(sched, "_send_to_channel", new_callable=AsyncMock):
                await sched._execute_job(job)

    # runner.process should NOT have been called (LightAgent used instead)
    mock_runner.process.assert_not_called()


@pytest.mark.asyncio
async def test_cron_job_legacy_fallback(store, mock_runner):
    """agent_prompt NULL → full runner is used."""
    sched = CronScheduler(store, mock_runner)
    job = CronJob(job_id="j1", user_id="u1", cron_expr="0 9 * * *", message="hi")

    await sched._execute_job(job)
    mock_runner.process.assert_called_once()


# ── Katman 3: Background task persistence ──────────────────


@pytest.mark.asyncio
async def test_background_task_persistence(store, mock_runner):
    """Worker saves result to DB + creates system_event."""
    worker = SubagentWorker(mock_runner, db=store)

    task_id = worker.spawn("u1", "research something", "api")
    await asyncio.sleep(0.2)

    task = store.get_background_task(task_id)
    assert task is not None
    assert task["status"] == "completed"
    assert task["result"] == "Done"

    events = store.get_undelivered_events("u1")
    assert len(events) == 1
    assert events[0]["source"] == f"task:{task_id}"
    assert events[0]["event_type"] == "task_completed"


def test_create_alert_tool(store, mock_runner):
    """create_alert tool creates a cron job with notify_skip config."""
    from graphbot.agent.tools.cron_tool import make_cron_tools

    sched = CronScheduler(store, mock_runner)
    with patch.object(sched, "_scheduler"):
        tools = make_cron_tools(sched)
        alert = next(t for t in tools if t.name == "create_alert")
        result = alert.invoke({
            "user_id": "u1",
            "cron_expr": "*/10 * * * *",
            "check_message": "Check gold price",
        })

    assert "Alert created" in result
    jobs = store.get_cron_jobs("u1")
    assert len(jobs) == 1
    assert jobs[0]["notify_condition"] == "notify_skip"
    assert jobs[0]["agent_prompt"] is not None

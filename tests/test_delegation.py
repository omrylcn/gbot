"""Tests for delegation planner, tool registry, and scheduler tool fix."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from graphbot.agent.delegation import DelegationPlanner
from graphbot.agent.tools.delegate import make_delegate_tools
from graphbot.agent.tools.registry import (
    build_background_tool_registry,
    get_tool_catalog,
    resolve_tools,
)
from graphbot.core.config import Config
from graphbot.core.cron.scheduler import CronScheduler
from graphbot.memory.store import MemoryStore


@pytest.fixture
def cfg(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return Config(assistant={"workspace": str(ws)})


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(str(tmp_path / "test.db"))
    s.get_or_create_user("u1", "Test User")
    return s


# ── Tool Registry ─────────────────────────────────────────


def test_build_registry_has_web_tools(cfg):
    """Registry includes web tools even without db."""
    registry = build_background_tool_registry(cfg)
    assert "web_search" in registry
    assert "web_fetch" in registry


def test_build_registry_has_memory_tools(cfg, store):
    """Registry with db includes memory and search tools."""
    registry = build_background_tool_registry(cfg, store)
    assert "web_search" in registry
    assert "save_user_note" in registry
    assert "search_items" in registry


def test_build_registry_excludes_meta_tools(cfg, store):
    """Registry should NOT include delegate, cron, reminder tools."""
    registry = build_background_tool_registry(cfg, store)
    names = set(registry.keys())
    for excluded in ("delegate", "add_cron_job", "set_reminder", "shell"):
        assert excluded not in names


def test_resolve_tools_by_names(cfg):
    """Resolve explicit tool names to tool objects."""
    registry = build_background_tool_registry(cfg)
    tools = resolve_tools(registry, ["web_search"])
    assert len(tools) == 1
    assert tools[0].name == "web_search"


def test_resolve_tools_default(cfg):
    """None tool_names with default → default tools."""
    registry = build_background_tool_registry(cfg)
    tools = resolve_tools(registry, None, default=["web_search", "web_fetch"])
    names = [t.name for t in tools]
    assert "web_search" in names
    assert "web_fetch" in names


def test_resolve_tools_missing_skipped(cfg):
    """Unknown tool names are silently skipped."""
    registry = build_background_tool_registry(cfg)
    tools = resolve_tools(registry, ["web_search", "nonexistent_tool"])
    assert len(tools) == 1
    assert tools[0].name == "web_search"


def test_resolve_tools_none_no_default(cfg):
    """None tool_names without default → empty list."""
    registry = build_background_tool_registry(cfg)
    tools = resolve_tools(registry, None)
    assert tools == []


def test_get_tool_catalog(cfg):
    """Catalog has one line per tool with name and description."""
    registry = build_background_tool_registry(cfg)
    catalog = get_tool_catalog(registry)
    assert "- web_search:" in catalog
    assert "- web_fetch:" in catalog
    lines = catalog.strip().split("\n")
    assert len(lines) >= 2


# ── DelegationPlanner ─────────────────────────────────────


def test_planner_parse_valid(cfg):
    """Valid JSON → correct dict with execution and processor types."""
    planner = DelegationPlanner(cfg, "- web_search: search")
    result = planner._parse(json.dumps({
        "execution": "immediate",
        "processor": "agent",
        "tools": ["web_search"],
        "prompt": "Search the web.",
        "model": "openai/gpt-4o-mini",
    }))
    assert result["execution"] == "immediate"
    assert result["processor"] == "agent"
    assert result["tools"] == ["web_search"]
    assert result["prompt"] == "Search the web."
    assert result["model"] == "openai/gpt-4o-mini"


def test_planner_parse_code_block(cfg):
    """JSON wrapped in markdown code block → parsed correctly."""
    planner = DelegationPlanner(cfg, "")
    text = '```json\n{"tools": ["web_fetch"], "prompt": "Fetch it.", "model": null}\n```'
    result = planner._parse(text)
    assert result["tools"] == ["web_fetch"]
    assert result["prompt"] == "Fetch it."
    assert result["model"] is None


def test_planner_parse_null_string_model(cfg):
    """LLM returning literal string 'null' for model → treated as None."""
    planner = DelegationPlanner(cfg, "")
    result = planner._parse(json.dumps({
        "tools": ["web_search"],
        "prompt": "Do research.",
        "model": "null",
    }))
    assert result["model"] is None


def test_planner_parse_fallback(cfg):
    """Broken JSON → fallback defaults."""
    planner = DelegationPlanner(cfg, "")
    result = planner._parse("this is not json")
    assert result["tools"] == ["web_search", "web_fetch"]
    assert "task" in result["prompt"].lower() or "complete" in result["prompt"].lower()
    assert result["model"] is None


@pytest.mark.asyncio
async def test_planner_plan_calls_llm(cfg):
    """plan() calls LLM and returns parsed result."""
    planner = DelegationPlanner(cfg, "- web_search: Search the web")
    mock_response = AsyncMock()
    mock_response.content = json.dumps({
        "tools": ["web_search"],
        "prompt": "Research the topic.",
        "model": None,
    })
    with patch("graphbot.agent.delegation.llm_provider.achat", return_value=mock_response):
        result = await planner.plan("Bitcoin fiyatını araştır")
    assert result["tools"] == ["web_search"]
    assert result["prompt"] == "Research the topic."


# ── CronScheduler _parse_tools fix ────────────────────────


def test_scheduler_parse_tools_with_registry(cfg, store):
    """CronScheduler with config resolves tool names correctly."""
    runner = AsyncMock()
    sched = CronScheduler(store, runner, config=cfg)
    tools = sched._parse_tools(json.dumps(["web_search", "web_fetch"]))
    names = [t.name for t in tools]
    assert "web_search" in names
    assert "web_fetch" in names


def test_scheduler_parse_tools_none():
    """None agent_tools → empty list."""
    sched = CronScheduler.__new__(CronScheduler)
    sched._registry = {}
    tools = sched._parse_tools(None)
    assert tools == []


def test_scheduler_parse_tools_invalid_json(cfg, store):
    """Invalid JSON → empty list."""
    runner = AsyncMock()
    sched = CronScheduler(store, runner, config=cfg)
    tools = sched._parse_tools("not-json")
    assert tools == []


# ── Delegate tool with planner ─────────────────────────────


@pytest.mark.asyncio
async def test_delegate_with_planner(cfg):
    """delegate tool with planner calls planner.plan then routes correctly."""
    from graphbot.core.background.worker import SubagentWorker

    worker = SubagentWorker(cfg)
    planner = AsyncMock()
    planner.plan = AsyncMock(return_value={
        "execution": "immediate",
        "processor": "agent",
        "tools": ["web_search"],
        "prompt": "Research it.",
        "model": None,
    })

    tools = make_delegate_tools(worker, planner=planner)
    assert len(tools) == 3  # delegate, list_scheduled_tasks, cancel_scheduled_task

    delegate_tool = next(t for t in tools if t.name == "delegate")
    with patch.object(worker, "spawn", return_value="abc123") as mock_spawn:
        result = await delegate_tool.ainvoke({
            "user_id": "u1",
            "task": "Bitcoin fiyatı",
        })
        assert "abc123" in result
        mock_spawn.assert_called_once_with(
            "u1", "Bitcoin fiyatı", "api",
            tools=["web_search"],
            prompt="Research it.",
            model=None,
        )
    planner.plan.assert_called_once_with("Bitcoin fiyatı")


# ── Planner parse — new execution/processor combinations ──


def test_planner_parse_delayed_static(cfg):
    """Delayed/static plan parses correctly."""
    planner = DelegationPlanner(cfg, "")
    result = planner._parse(json.dumps({
        "execution": "delayed",
        "processor": "static",
        "delay_seconds": 7200,
        "cron_expr": None,
        "message": "Reminder: you have a meeting!",
        "tool_name": None,
        "tool_args": None,
        "tools": [],
        "prompt": None,
        "model": None,
    }))
    assert result["execution"] == "delayed"
    assert result["processor"] == "static"
    assert result["delay_seconds"] == 7200
    assert result["message"] == "Reminder: you have a meeting!"
    assert result["tools"] == []


def test_planner_parse_delayed_function(cfg):
    """Delayed/function plan parses correctly."""
    planner = DelegationPlanner(cfg, "")
    result = planner._parse(json.dumps({
        "execution": "delayed",
        "processor": "function",
        "delay_seconds": 300,
        "cron_expr": None,
        "message": None,
        "tool_name": "send_message_to_user",
        "tool_args": {"target_user": "Murat", "message": "hello"},
        "tools": [],
        "prompt": None,
        "model": None,
    }))
    assert result["execution"] == "delayed"
    assert result["processor"] == "function"
    assert result["delay_seconds"] == 300
    assert result["tool_name"] == "send_message_to_user"
    assert result["tool_args"] == {"target_user": "Murat", "message": "hello"}


def test_planner_parse_recurring_agent(cfg):
    """Recurring/agent plan parses correctly."""
    planner = DelegationPlanner(cfg, "")
    result = planner._parse(json.dumps({
        "execution": "recurring",
        "processor": "agent",
        "delay_seconds": None,
        "cron_expr": "0 9 * * *",
        "message": None,
        "tool_name": None,
        "tool_args": None,
        "tools": ["web_search", "send_message_to_user"],
        "prompt": "Check weather and send summary to user.",
        "model": None,
    }))
    assert result["execution"] == "recurring"
    assert result["processor"] == "agent"
    assert result["cron_expr"] == "0 9 * * *"
    assert "web_search" in result["tools"]
    assert "send_message_to_user" in result["tools"]


def test_planner_parse_monitor_agent(cfg):
    """Monitor/agent plan parses correctly."""
    planner = DelegationPlanner(cfg, "")
    result = planner._parse(json.dumps({
        "execution": "monitor",
        "processor": "agent",
        "delay_seconds": None,
        "cron_expr": "*/30 * * * *",
        "message": None,
        "tool_name": None,
        "tool_args": None,
        "tools": ["web_fetch", "send_message_to_user"],
        "prompt": "Check gold price. If above $3000, report. Otherwise [SKIP].",
        "model": None,
    }))
    assert result["execution"] == "monitor"
    assert result["processor"] == "agent"
    assert result["cron_expr"] == "*/30 * * * *"
    assert "[SKIP]" in result["prompt"]


def test_planner_parse_recurring_function(cfg):
    """Recurring/function plan parses correctly."""
    planner = DelegationPlanner(cfg, "")
    result = planner._parse(json.dumps({
        "execution": "recurring",
        "processor": "function",
        "delay_seconds": None,
        "cron_expr": "*/10 * * * *",
        "message": None,
        "tool_name": "send_message_to_user",
        "tool_args": {"target_user": "Zeynep", "message": "hello"},
        "tools": [],
        "prompt": None,
        "model": None,
    }))
    assert result["execution"] == "recurring"
    assert result["processor"] == "function"
    assert result["cron_expr"] == "*/10 * * * *"
    assert result["tool_name"] == "send_message_to_user"


def test_planner_parse_invalid_execution(cfg):
    """Invalid execution type falls back to 'immediate'."""
    planner = DelegationPlanner(cfg, "")
    result = planner._parse(json.dumps({
        "execution": "invalid_type",
        "processor": "agent",
        "tools": ["web_search"],
        "prompt": "Do something.",
        "model": None,
    }))
    assert result["execution"] == "immediate"


def test_planner_parse_invalid_processor(cfg):
    """Invalid processor type falls back to 'agent'."""
    planner = DelegationPlanner(cfg, "")
    result = planner._parse(json.dumps({
        "execution": "immediate",
        "processor": "invalid_proc",
        "tools": ["web_search"],
        "prompt": "Do something.",
        "model": None,
    }))
    assert result["processor"] == "agent"


# ── Delegate routing — delayed/recurring/monitor ──────────


@pytest.mark.asyncio
async def test_delegate_delayed_static(cfg, store):
    """Delayed/static → scheduler.add_reminder with processor=static."""
    runner = AsyncMock()
    sched = CronScheduler(store, runner, config=cfg)
    planner = AsyncMock()
    planner.plan = AsyncMock(return_value={
        "execution": "delayed",
        "processor": "static",
        "delay_seconds": 120,
        "cron_expr": None,
        "message": "Meeting reminder!",
        "tool_name": None,
        "tool_args": None,
        "tools": [],
        "prompt": None,
        "model": None,
    })

    tools = make_delegate_tools(scheduler=sched, planner=planner, db=store)
    delegate_tool = next(t for t in tools if t.name == "delegate")

    with patch.object(sched, "add_reminder", return_value={"reminder_id": "rem123"}) as mock_add:
        result = await delegate_tool.ainvoke({
            "user_id": "u1",
            "task": "2 dk sonra toplantı hatırlat",
            "channel": "telegram",
        })
        assert "rem123" in result
        assert "delayed" in result
        mock_add.assert_called_once()
        call_kwargs = mock_add.call_args
        assert call_kwargs[0][0] == "u1"  # user_id
        assert call_kwargs[0][1] == "telegram"  # channel
        assert call_kwargs[0][2] == 120  # delay_seconds
        assert call_kwargs.kwargs["processor"] == "static"


@pytest.mark.asyncio
async def test_delegate_delayed_function(cfg, store):
    """Delayed/function → scheduler.add_reminder with processor=function."""
    runner = AsyncMock()
    sched = CronScheduler(store, runner, config=cfg)
    planner = AsyncMock()
    planner.plan = AsyncMock(return_value={
        "execution": "delayed",
        "processor": "function",
        "delay_seconds": 300,
        "cron_expr": None,
        "message": None,
        "tool_name": "send_message_to_user",
        "tool_args": {"target_user": "Murat", "message": "naber"},
        "tools": [],
        "prompt": None,
        "model": None,
    })

    tools = make_delegate_tools(scheduler=sched, planner=planner, db=store)
    delegate_tool = next(t for t in tools if t.name == "delegate")

    with patch.object(sched, "add_reminder", return_value={"reminder_id": "rem456"}) as mock_add:
        result = await delegate_tool.ainvoke({
            "user_id": "u1",
            "task": "5 dk sonra Murat'a naber yaz",
            "channel": "telegram",
        })
        assert "rem456" in result
        mock_add.assert_called_once()
        assert mock_add.call_args.kwargs["processor"] == "function"


@pytest.mark.asyncio
async def test_delegate_recurring_agent(cfg, store):
    """Recurring/agent → scheduler.add_job with notify_condition=always."""
    runner = AsyncMock()
    sched = CronScheduler(store, runner, config=cfg)
    planner = AsyncMock()
    planner.plan = AsyncMock(return_value={
        "execution": "recurring",
        "processor": "agent",
        "delay_seconds": None,
        "cron_expr": "0 9 * * *",
        "message": None,
        "tool_name": None,
        "tool_args": None,
        "tools": ["web_search", "send_message_to_user"],
        "prompt": "Check weather and report.",
        "model": None,
    })

    tools = make_delegate_tools(scheduler=sched, planner=planner, db=store)
    delegate_tool = next(t for t in tools if t.name == "delegate")

    mock_job = AsyncMock()
    mock_job.job_id = "cron789"
    with patch.object(sched, "add_job", return_value=mock_job) as mock_add:
        result = await delegate_tool.ainvoke({
            "user_id": "u1",
            "task": "Her sabah 9'da hava durumunu bildir",
            "channel": "telegram",
        })
        assert "cron789" in result
        assert "recurring" in result
        mock_add.assert_called_once()
        assert mock_add.call_args.kwargs["processor"] == "agent"
        assert mock_add.call_args.kwargs["notify_condition"] == "always"


@pytest.mark.asyncio
async def test_delegate_monitor_agent(cfg, store):
    """Monitor/agent → scheduler.add_job with notify_condition=notify_skip."""
    runner = AsyncMock()
    sched = CronScheduler(store, runner, config=cfg)
    planner = AsyncMock()
    planner.plan = AsyncMock(return_value={
        "execution": "monitor",
        "processor": "agent",
        "delay_seconds": None,
        "cron_expr": "*/30 * * * *",
        "message": None,
        "tool_name": None,
        "tool_args": None,
        "tools": ["web_fetch", "send_message_to_user"],
        "prompt": "Check gold price. If above $3000, report. Otherwise [SKIP].",
        "model": None,
    })

    tools = make_delegate_tools(scheduler=sched, planner=planner, db=store)
    delegate_tool = next(t for t in tools if t.name == "delegate")

    mock_job = AsyncMock()
    mock_job.job_id = "mon001"
    with patch.object(sched, "add_job", return_value=mock_job) as mock_add:
        result = await delegate_tool.ainvoke({
            "user_id": "u1",
            "task": "Altın 3000 geçince haber ver",
            "channel": "telegram",
        })
        assert "mon001" in result
        assert "monitor" in result
        mock_add.assert_called_once()
        assert mock_add.call_args.kwargs["notify_condition"] == "notify_skip"


# ── Processor execution tests ────────────────────────────


@pytest.mark.asyncio
async def test_run_by_processor_static(cfg, store):
    """Static processor returns message text with should_deliver=True."""
    runner = AsyncMock()
    sched = CronScheduler(store, runner, config=cfg)
    plan = {"message": "Time for the meeting!"}
    text, deliver = await sched._run_by_processor(
        "static", plan, "reminder message", "u1", "telegram",
    )
    assert text == "Time for the meeting!"
    assert deliver is True


@pytest.mark.asyncio
async def test_run_by_processor_static_fallback(cfg, store):
    """Static processor with no message falls back to 'Hatirlatma: ...'."""
    runner = AsyncMock()
    sched = CronScheduler(store, runner, config=cfg)
    plan = {}
    text, deliver = await sched._run_by_processor(
        "static", plan, "toplantı var", "u1", "telegram",
    )
    assert "Hatirlatma" in text
    assert "toplantı var" in text
    assert deliver is True


@pytest.mark.asyncio
async def test_run_by_processor_function(cfg, store):
    """Function processor calls tool directly, returns (None, False)."""
    runner = AsyncMock()
    sched = CronScheduler(store, runner, config=cfg)

    mock_tool = AsyncMock()
    mock_tool.name = "send_message_to_user"
    sched._registry = {"send_message_to_user": mock_tool}

    plan = {
        "tool_name": "send_message_to_user",
        "tool_args": {"target_user": "Murat", "message": "naber"},
    }
    text, deliver = await sched._run_by_processor(
        "function", plan, "task msg", "u1", "telegram",
    )
    assert text is None
    assert deliver is False
    mock_tool.ainvoke.assert_called_once()
    call_args = mock_tool.ainvoke.call_args[0][0]
    assert call_args["target_user"] == "Murat"
    assert call_args["message"] == "naber"


@pytest.mark.asyncio
async def test_run_by_processor_agent(cfg, store):
    """Agent processor runs LightAgent, returns (text, False) — agent delivers."""
    runner = AsyncMock()
    sched = CronScheduler(store, runner, config=cfg)

    plan = {
        "prompt": "Check weather and report.",
        "tools": ["web_search"],
        "model": None,
    }
    with patch("graphbot.agent.light.LightAgent") as MockAgent:
        mock_instance = AsyncMock()
        mock_instance.run_with_meta = AsyncMock(return_value=("Sunny, 22°C", 150, {"send_message_to_user"}))
        MockAgent.return_value = mock_instance

        text, deliver = await sched._run_by_processor(
            "agent", plan, "check weather", "u1", "telegram",
        )
        assert text == "Sunny, 22°C"
        assert deliver is False  # Agent called send_message_to_user
        MockAgent.assert_called_once()


# ── list and cancel tools ─────────────────────────────────


@pytest.mark.asyncio
async def test_list_scheduled_tasks(cfg, store):
    """list_scheduled_tasks returns cron jobs and reminders."""
    runner = AsyncMock()
    sched = CronScheduler(store, runner, config=cfg)

    tools = make_delegate_tools(scheduler=sched, db=store)
    list_tool = next(t for t in tools if t.name == "list_scheduled_tasks")

    with patch.object(sched, "list_jobs", return_value=[
        {"job_id": "j1", "cron_expr": "0 9 * * *", "message": "Weather check", "enabled": 1, "processor": "agent"},
    ]), patch.object(sched, "list_reminders", return_value=[
        {"reminder_id": "r1", "run_at": "2025-01-01T10:00:00", "message": "Meeting", "processor": "static"},
    ]):
        result = list_tool.invoke({"user_id": "u1"})
        assert "j1" in result
        assert "r1" in result
        assert "Weather check" in result
        assert "Meeting" in result


@pytest.mark.asyncio
async def test_cancel_cron_task(cfg, store):
    """cancel_scheduled_task with cron: prefix removes cron job."""
    runner = AsyncMock()
    sched = CronScheduler(store, runner, config=cfg)

    tools = make_delegate_tools(scheduler=sched, db=store)
    cancel_tool = next(t for t in tools if t.name == "cancel_scheduled_task")

    with patch.object(sched, "remove_job") as mock_remove:
        result = cancel_tool.invoke({"task_id": "cron:abc123"})
        assert "abc123" in result
        assert "removed" in result.lower()
        mock_remove.assert_called_once_with("abc123")


@pytest.mark.asyncio
async def test_cancel_reminder_task(cfg, store):
    """cancel_scheduled_task with reminder: prefix cancels reminder."""
    runner = AsyncMock()
    sched = CronScheduler(store, runner, config=cfg)

    tools = make_delegate_tools(scheduler=sched, db=store)
    cancel_tool = next(t for t in tools if t.name == "cancel_scheduled_task")

    with patch.object(sched, "cancel_reminder", return_value=True) as mock_cancel:
        result = cancel_tool.invoke({"task_id": "reminder:def456"})
        assert "def456" in result
        assert "cancelled" in result.lower()
        mock_cancel.assert_called_once_with("def456")


# ── Delegation log ────────────────────────────────────────


def test_delegation_log_crud(store):
    """Test delegation_log write and read."""
    store.log_delegation("u1", "Research BTC", "immediate", "agent", reference_id="bg:abc")
    logs = store.get_delegation_log("u1")
    assert len(logs) >= 1
    assert logs[0]["task_description"] == "Research BTC"
    assert logs[0]["execution_type"] == "immediate"
    assert logs[0]["processor_type"] == "agent"


# ── Runner processor (self-reminder) ─────────────────────


def test_planner_parse_runner(cfg):
    """Runner processor parses correctly."""
    planner = DelegationPlanner(cfg, "")
    result = planner._parse(json.dumps({
        "execution": "recurring",
        "processor": "runner",
        "delay_seconds": None,
        "cron_expr": "0 10 * * *",
        "message": None,
        "tool_name": None,
        "tool_args": None,
        "tools": [],
        "prompt": None,
        "model": None,
    }))
    assert result["execution"] == "recurring"
    assert result["processor"] == "runner"
    assert result["cron_expr"] == "0 10 * * *"
    assert result["tools"] == []
    assert result["prompt"] is None


@pytest.mark.asyncio
async def test_run_by_processor_runner(cfg, store):
    """Runner processor calls runner.process with full context."""
    runner = AsyncMock()
    runner.process = AsyncMock(
        return_value=("İftar 17:47, Zeynep'e gönderdim.", "sess-1")
    )
    sched = CronScheduler(store, runner, config=cfg)

    text, deliver = await sched._run_by_processor(
        "runner", {}, "iftar saatini bul, Zeynep'e gönder", "owner", "whatsapp",
    )

    runner.process.assert_called_once_with(
        user_id="owner",
        channel="whatsapp",
        message="iftar saatini bul, Zeynep'e gönder",
    )
    assert "İftar" in text
    assert deliver is True


@pytest.mark.asyncio
async def test_delegate_runner_immediate_downgrade(cfg, store):
    """immediate+runner downgrades to immediate+agent (loop prevention)."""
    from graphbot.core.background.worker import SubagentWorker

    worker = SubagentWorker(cfg)
    planner = AsyncMock()
    planner.plan = AsyncMock(return_value={
        "execution": "immediate",
        "processor": "runner",
        "delay_seconds": None,
        "cron_expr": None,
        "message": None,
        "tool_name": None,
        "tool_args": None,
        "tools": [],
        "prompt": None,
        "model": None,
    })

    tools = make_delegate_tools(worker, planner=planner, db=store)
    delegate_tool = next(t for t in tools if t.name == "delegate")

    with patch.object(worker, "spawn", return_value="abc") as mock_spawn:
        await delegate_tool.ainvoke({"user_id": "u1", "task": "test"})
        # Should have been downgraded — worker.spawn called (not runner.process)
        mock_spawn.assert_called_once()

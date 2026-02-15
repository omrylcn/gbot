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
    """Valid JSON → correct dict."""
    planner = DelegationPlanner(cfg, "- web_search: search")
    result = planner._parse(json.dumps({
        "tools": ["web_search"],
        "prompt": "Search the web.",
        "model": "openai/gpt-4o-mini",
    }))
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
    """delegate tool with planner calls planner.plan then worker.spawn."""
    from graphbot.core.background.worker import SubagentWorker

    worker = SubagentWorker(cfg)
    planner = AsyncMock()
    planner.plan = AsyncMock(return_value={
        "tools": ["web_search"],
        "prompt": "Research it.",
        "model": None,
    })

    tools = make_delegate_tools(worker, planner)
    assert len(tools) == 1

    with patch.object(worker, "spawn", return_value="abc123") as mock_spawn:
        result = await tools[0].ainvoke({
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

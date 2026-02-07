"""Tests for graphbot.agent.tools (Faz 3)."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from graphbot.agent.tools import make_tools
from graphbot.agent.tools.filesystem import make_filesystem_tools
from graphbot.agent.tools.memory_tools import make_memory_tools
from graphbot.agent.tools.search import make_search_tools
from graphbot.agent.tools.shell import make_shell_tools, DENY_PATTERNS
from graphbot.agent.tools.web import make_web_tools
from graphbot.core.config import Config
from graphbot.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(str(tmp_path / "test.db"))


@pytest.fixture
def cfg(tmp_path):
    # Workspace points to a tmp dir for sandbox testing
    return Config(assistant={"workspace": str(tmp_path / "workspace")})


# --- Memory tools ---

def test_memory_tools_created(store):
    tools = make_memory_tools(store)
    assert len(tools) == 7
    names = {t.name for t in tools}
    assert "save_user_note" in names
    assert "get_user_context" in names
    assert "add_favorite" in names


def test_save_and_get_note(store):
    tools = make_memory_tools(store)
    save = next(t for t in tools if t.name == "save_user_note")
    get_ctx = next(t for t in tools if t.name == "get_user_context")

    result = save.invoke({"user_id": "u1", "note": "likes coffee"})
    assert "saved" in result.lower()

    ctx = get_ctx.invoke({"user_id": "u1"})
    assert "coffee" in ctx


def test_activity_logging(store):
    tools = make_memory_tools(store)
    log = next(t for t in tools if t.name == "log_activity")
    recent = next(t for t in tools if t.name == "get_recent_activities")

    log.invoke({"user_id": "u1", "item_title": "Python Tutorial", "item_id": "i1"})
    result = recent.invoke({"user_id": "u1"})
    assert "Python Tutorial" in result


def test_favorites_crud(store):
    tools = make_memory_tools(store)
    add = next(t for t in tools if t.name == "add_favorite")
    get = next(t for t in tools if t.name == "get_favorites")
    remove = next(t for t in tools if t.name == "remove_favorite")

    add.invoke({"user_id": "u1", "item_id": "i1", "item_title": "Item 1"})
    result = get.invoke({"user_id": "u1"})
    assert "Item 1" in result

    # Duplicate check
    dup = add.invoke({"user_id": "u1", "item_id": "i1", "item_title": "Item 1"})
    assert "already" in dup.lower()

    remove.invoke({"user_id": "u1", "item_id": "i1"})
    result = get.invoke({"user_id": "u1"})
    assert "No favorites" in result


# --- Search tools ---

def test_search_mock():
    tools = make_search_tools()
    assert len(tools) == 2
    search = next(t for t in tools if t.name == "search_items")
    result = search.invoke({"query": "test"})
    assert "mock" in result.lower()


# --- Filesystem tools ---

def test_filesystem_read_write(cfg, tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    tools = make_filesystem_tools(cfg)
    write = next(t for t in tools if t.name == "write_file")
    read = next(t for t in tools if t.name == "read_file")
    ls = next(t for t in tools if t.name == "list_dir")

    write.invoke({"path": str(ws / "hello.txt"), "content": "Hello World"})
    result = read.invoke({"path": str(ws / "hello.txt")})
    assert "Hello World" in result

    ls_result = ls.invoke({"path": str(ws)})
    assert "hello.txt" in ls_result


def test_filesystem_sandbox(cfg, tmp_path):
    tools = make_filesystem_tools(cfg)
    read = next(t for t in tools if t.name == "read_file")
    result = read.invoke({"path": "/etc/passwd"})
    assert "denied" in result.lower() or "outside" in result.lower()


# --- Shell tools ---

@pytest.mark.asyncio
async def test_shell_exec(cfg):
    tools = make_shell_tools(cfg)
    exec_cmd = tools[0]
    result = await exec_cmd.ainvoke({"command": "echo hello"})
    assert "hello" in result


@pytest.mark.asyncio
async def test_shell_deny(cfg):
    tools = make_shell_tools(cfg)
    exec_cmd = tools[0]
    result = await exec_cmd.ainvoke({"command": "rm -rf /"})
    assert "blocked" in result.lower()


# --- Web tools ---

def test_web_tools_created(cfg):
    tools = make_web_tools(cfg)
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert "web_search" in names
    assert "web_fetch" in names


# --- Integration ---

def test_make_tools_all(cfg, store):
    tools = make_tools(cfg, store)
    assert len(tools) == 16  # 7+2+4+1+2+0+0
    names = {t.name for t in tools}
    assert "save_user_note" in names
    assert "search_items" in names
    assert "read_file" in names
    assert "exec_command" in names
    assert "web_search" in names

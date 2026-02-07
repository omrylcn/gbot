"""Tests for graphbot.agent (Faz 2)."""

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from graphbot.agent.context import ContextBuilder
from graphbot.agent.graph import create_graph
from graphbot.agent.nodes import should_continue
from graphbot.agent.runner import GraphRunner
from graphbot.agent.state import AgentState
from graphbot.core.config import Config
from graphbot.memory.store import MemoryStore


@pytest.fixture
def cfg():
    return Config(assistant={"system_prompt": "You are TestBot."})


@pytest.fixture
def store(tmp_path):
    return MemoryStore(str(tmp_path / "test.db"))


# --- Graph ---

def test_graph_compiles(cfg, store):
    g = create_graph(cfg, store)
    assert g is not None
    assert hasattr(g, "ainvoke")


# --- ContextBuilder ---

def test_context_builder_system_prompt(cfg, store):
    builder = ContextBuilder(cfg, store)
    prompt = builder.build("u1")
    assert "You are TestBot." in prompt


def test_context_builder_layers(cfg, store):
    store.write_memory("long_term", "Remember: user likes coffee")
    store.add_note("u1", "vegetarian")
    builder = ContextBuilder(cfg, store)
    prompt = builder.build("u1")
    assert "coffee" in prompt
    assert "vegetarian" in prompt


def test_context_builder_default_identity(store):
    cfg = Config()
    builder = ContextBuilder(cfg, store)
    prompt = builder.build("u1")
    assert "GraphBot" in prompt


def test_context_builder_session_summary(cfg, store):
    sid = store.create_session("u1")
    store.end_session(sid, summary="We talked about Python.", close_reason="token_limit")
    builder = ContextBuilder(cfg, store)
    prompt = builder.build("u1")
    assert "Python" in prompt


# --- should_continue ---

def test_should_continue_respond():
    state = {
        "messages": [AIMessage(content="Hello")],
        "iteration": 1,
    }
    assert should_continue(state) == "respond"


def test_should_continue_tools():
    ai = AIMessage(
        content="",
        tool_calls=[{"id": "1", "name": "search", "args": {}}],
    )
    state = {"messages": [ai], "iteration": 1}
    assert should_continue(state) == "execute_tools"


def test_should_continue_max_iteration():
    ai = AIMessage(
        content="",
        tool_calls=[{"id": "1", "name": "search", "args": {}}],
    )
    state = {"messages": [ai], "iteration": 20}
    assert should_continue(state) == "respond"


# --- Runner helpers ---

def test_runner_load_history(cfg, store):
    sid = store.create_session("u1")
    store.add_message(sid, "user", "hi")
    store.add_message(sid, "assistant", "hello")

    runner = GraphRunner.__new__(GraphRunner)
    runner.db = store
    msgs = runner._load_history(sid)

    assert len(msgs) == 2
    assert isinstance(msgs[0], HumanMessage)
    assert isinstance(msgs[1], AIMessage)


def test_runner_extract_response():
    runner = GraphRunner.__new__(GraphRunner)
    state = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(content="thinking", tool_calls=[{"id": "1", "name": "t", "args": {}}]),
            ToolMessage(content="result", tool_call_id="1"),
            AIMessage(content="Final answer"),
        ]
    }
    assert runner._extract_response(state) == "Final answer"


@pytest.mark.asyncio
async def test_runner_process(cfg, store):
    """Full process flow with mocked LLM."""
    ai_msg = AIMessage(
        content="Merhaba!",
        response_metadata={"usage": {"total_tokens": 100}},
    )

    with patch("graphbot.agent.nodes.llm_provider.achat", new_callable=AsyncMock, return_value=ai_msg):
        runner = GraphRunner(cfg, store)
        response, session_id = await runner.process("u1", "api", "selam")

    assert response == "Merhaba!"
    assert session_id  # session_id returned
    # Messages saved
    msgs = store.get_session_messages(session_id)
    assert any(m["role"] == "user" for m in msgs)
    assert any(m["role"] == "assistant" for m in msgs)

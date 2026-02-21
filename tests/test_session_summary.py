"""Tests for Faz 17 — Session Summarization, Fact Extraction, Preferences."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from graphbot.agent.context import ContextBuilder
from graphbot.agent.runner import GraphRunner
from graphbot.agent.tools.memory_tools import make_memory_tools
from graphbot.core.config import Config
from graphbot.memory.store import MemoryStore


@pytest.fixture
def cfg():
    return Config(assistant={"system_prompt": "TestBot."})


@pytest.fixture
def store(tmp_path):
    return MemoryStore(str(tmp_path / "test.db"))


# ── _prepare_summary_messages ────────────────────────────────


def test_prepare_summary_messages_filters_tools():
    """Tool messages and empty-content messages are excluded."""
    db_msgs = [
        {"role": "user", "content": "Hello", "tool_calls": None},
        {"role": "assistant", "content": "", "tool_calls": '[{"id":"1"}]'},
        {"role": "tool", "content": "result data", "tool_calls": None},
        {"role": "assistant", "content": "Here is the answer", "tool_calls": None},
    ]
    result = GraphRunner._prepare_summary_messages(db_msgs)
    assert len(result) == 2
    assert result[0] == {"role": "user", "content": "Hello"}
    assert result[1] == {"role": "assistant", "content": "Here is the answer"}


def test_prepare_summary_messages_empty():
    """Empty list returns empty list."""
    assert GraphRunner._prepare_summary_messages([]) == []


# ── _rotate_session ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_rotate_session_calls_asummarize(cfg, store):
    """_rotate_session calls asummarize and saves summary to DB."""
    sid = store.create_session("u1", "api")
    store.add_message(sid, "user", "Tell me about Python")
    store.add_message(sid, "assistant", "Python is a great language.")

    runner = GraphRunner.__new__(GraphRunner)
    runner.db = store
    runner.config = cfg

    with patch(
        "graphbot.agent.runner.asummarize",
        new_callable=AsyncMock,
        return_value="User asked about Python.",
    ) as mock_sum, patch(
        "graphbot.agent.runner.aextract_facts",
        new_callable=AsyncMock,
        return_value={},
    ):
        await runner._rotate_session("u1", sid)

    mock_sum.assert_called_once()
    call_msgs = mock_sum.call_args[0][0]
    assert all(m["role"] in ("user", "assistant") for m in call_msgs)

    session = store.get_session(sid)
    assert session["ended_at"] is not None
    assert session["summary"] == "User asked about Python."
    assert session["close_reason"] == "token_limit"


@pytest.mark.asyncio
async def test_rotate_session_fallback_on_failure(cfg, store):
    """When asummarize returns empty string, fallback summary is saved."""
    sid = store.create_session("u1", "api")
    store.add_message(sid, "user", "hi")
    store.add_message(sid, "assistant", "hello")

    runner = GraphRunner.__new__(GraphRunner)
    runner.db = store
    runner.config = cfg

    with patch(
        "graphbot.agent.runner.asummarize",
        new_callable=AsyncMock,
        return_value="",
    ), patch(
        "graphbot.agent.runner.aextract_facts",
        new_callable=AsyncMock,
        return_value={},
    ):
        await runner._rotate_session("u1", sid)

    session = store.get_session(sid)
    assert session["ended_at"] is not None
    assert "summary unavailable" in session["summary"]


@pytest.mark.asyncio
async def test_rotate_session_no_messages(cfg, store):
    """Session with only tool messages skips asummarize call."""
    sid = store.create_session("u1", "api")
    store.add_message(sid, "tool", "some result", tool_call_id="tc1")

    runner = GraphRunner.__new__(GraphRunner)
    runner.db = store
    runner.config = cfg

    with patch(
        "graphbot.agent.runner.asummarize",
        new_callable=AsyncMock,
    ) as mock_sum, patch(
        "graphbot.agent.runner.aextract_facts",
        new_callable=AsyncMock,
    ) as mock_ext:
        await runner._rotate_session("u1", sid)

    mock_sum.assert_not_called()
    mock_ext.assert_not_called()
    session = store.get_session(sid)
    assert session["ended_at"] is not None
    assert "summary unavailable" in session["summary"]


@pytest.mark.asyncio
async def test_rotate_session_always_closes(cfg, store):
    """Session is closed even if asummarize raises an exception."""
    sid = store.create_session("u1", "api")
    store.add_message(sid, "user", "test")

    runner = GraphRunner.__new__(GraphRunner)
    runner.db = store
    runner.config = cfg

    with patch(
        "graphbot.agent.runner.asummarize",
        new_callable=AsyncMock,
        side_effect=Exception("LLM down"),
    ), patch(
        "graphbot.agent.runner.aextract_facts",
        new_callable=AsyncMock,
        return_value={},
    ):
        await runner._rotate_session("u1", sid)

    session = store.get_session(sid)
    assert session["ended_at"] is not None


# ── Fact extraction ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_rotate_session_extracts_facts(cfg, store):
    """Facts from aextract_facts are saved to DB tables."""
    store.get_or_create_user("u1")
    sid = store.create_session("u1", "api")
    store.add_message(sid, "user", "I prefer dark mode and I'm a developer")
    store.add_message(sid, "assistant", "Noted!")

    runner = GraphRunner.__new__(GraphRunner)
    runner.db = store
    runner.config = cfg

    facts = {
        "preferences": [{"key": "theme", "value": "dark"}],
        "notes": ["User is a software developer"],
    }

    with patch(
        "graphbot.agent.runner.asummarize",
        new_callable=AsyncMock,
        return_value="Summary text.",
    ), patch(
        "graphbot.agent.runner.aextract_facts",
        new_callable=AsyncMock,
        return_value=facts,
    ):
        await runner._rotate_session("u1", sid)

    # Preferences saved
    prefs = store.get_preferences("u1")
    assert prefs.get("theme") == "dark"

    # Notes saved with source="extraction"
    notes = store.get_notes("u1")
    assert any("developer" in n for n in notes)


@pytest.mark.asyncio
async def test_fact_extraction_failure_doesnt_block(cfg, store):
    """If aextract_facts fails, summary is still saved and session closed."""
    sid = store.create_session("u1", "api")
    store.add_message(sid, "user", "test")

    runner = GraphRunner.__new__(GraphRunner)
    runner.db = store
    runner.config = cfg

    with patch(
        "graphbot.agent.runner.asummarize",
        new_callable=AsyncMock,
        return_value="Good summary.",
    ), patch(
        "graphbot.agent.runner.aextract_facts",
        new_callable=AsyncMock,
        side_effect=Exception("JSON parse error"),
    ):
        await runner._rotate_session("u1", sid)

    session = store.get_session(sid)
    assert session["ended_at"] is not None
    assert session["summary"] == "Good summary."


def test_save_extracted_facts_preferences(cfg, store):
    """Preferences from facts dict are merged into DB."""
    store.get_or_create_user("u1")
    runner = GraphRunner.__new__(GraphRunner)
    runner.db = store

    facts = {
        "preferences": [
            {"key": "language", "value": "Turkish"},
            {"key": "tone", "value": "casual"},
        ]
    }
    runner._save_extracted_facts("u1", facts)

    prefs = store.get_preferences("u1")
    assert prefs["language"] == "Turkish"
    assert prefs["tone"] == "casual"


def test_save_extracted_facts_notes(cfg, store):
    """Notes from facts are saved with source='extraction'."""
    store.get_or_create_user("u1")
    runner = GraphRunner.__new__(GraphRunner)
    runner.db = store

    facts = {"notes": ["Works at Acme Corp", "Interested in AI"]}
    runner._save_extracted_facts("u1", facts)

    notes = store.get_notes("u1")
    assert len(notes) >= 2
    assert any("Acme" in n for n in notes)
    assert any("AI" in n for n in notes)


def test_save_extracted_facts_empty(cfg, store):
    """Empty facts dict does nothing (no errors)."""
    runner = GraphRunner.__new__(GraphRunner)
    runner.db = store
    runner._save_extracted_facts("u1", {})


def test_save_extracted_facts_malformed(cfg, store):
    """Malformed preference entries are skipped gracefully."""
    store.get_or_create_user("u1")
    runner = GraphRunner.__new__(GraphRunner)
    runner.db = store

    facts = {
        "preferences": [
            {"key": "valid", "value": "yes"},
            "not a dict",
            {"missing_value": True},
        ],
        "notes": ["real note", 123, None],
    }
    runner._save_extracted_facts("u1", facts)

    prefs = store.get_preferences("u1")
    assert prefs.get("valid") == "yes"
    assert len(prefs) == 1

    notes = store.get_notes("u1")
    assert len(notes) == 1


# ── Closed session reuse bug fix ─────────────────────────────


@pytest.mark.asyncio
async def test_closed_session_creates_new(cfg, store):
    """Providing a closed session_id creates a new session."""
    old_sid = store.create_session("u1", "api")
    store.add_message(old_sid, "user", "old message")
    store.end_session(old_sid, summary="old summary", close_reason="token_limit")

    ai_msg = AIMessage(
        content="Merhaba!",
        response_metadata={"usage": {"total_tokens": 50}},
    )

    with patch(
        "graphbot.agent.nodes.llm_provider.achat",
        new_callable=AsyncMock,
        return_value=ai_msg,
    ):
        runner = GraphRunner(cfg, store)
        response, new_sid = await runner.process(
            "u1", "api", "hello", session_id=old_sid
        )

    assert new_sid != old_sid
    assert response == "Merhaba!"
    # Old session untouched
    assert store.get_session(old_sid)["ended_at"] is not None
    # New session is open
    assert store.get_session(new_sid)["ended_at"] is None


# ── Summary in context ───────────────────────────────────────


def test_summary_in_new_session_context(cfg, store):
    """ContextBuilder injects previous session summary into new session."""
    sid = store.create_session("u1", "api")
    store.end_session(
        sid,
        summary="User discussed Django and prefers dark mode.",
        close_reason="token_limit",
    )

    builder = ContextBuilder(cfg, store)
    prompt = builder.build("u1")
    assert "Previous Conversation" in prompt
    assert "Django" in prompt


# ── Preference tools ─────────────────────────────────────────


def test_preference_tools_set_get_remove(store):
    """set, get, remove preference tools work correctly."""
    store.get_or_create_user("u1")
    tools = make_memory_tools(store)
    tool_map = {t.name: t for t in tools}

    # set
    result = tool_map["set_user_preference"].invoke(
        {"user_id": "u1", "key": "language", "value": "Turkish"}
    )
    assert "Preference saved" in result

    # get
    result = tool_map["get_user_preferences"].invoke({"user_id": "u1"})
    assert "language" in result
    assert "Turkish" in result

    # remove
    result = tool_map["remove_user_preference"].invoke(
        {"user_id": "u1", "key": "language"}
    )
    assert "removed" in result

    # verify gone
    result = tool_map["get_user_preferences"].invoke({"user_id": "u1"})
    assert "language" not in result


def test_preference_remove_nonexistent(store):
    """Removing a nonexistent preference returns not found."""
    store.get_or_create_user("u1")
    tools = make_memory_tools(store)
    tool_map = {t.name: t for t in tools}

    result = tool_map["remove_user_preference"].invoke(
        {"user_id": "u1", "key": "nonexistent"}
    )
    assert "not found" in result


# ── E2E: process triggers full rotation ──────────────────────


@pytest.mark.asyncio
async def test_process_triggers_full_rotation(store):
    """process() calls _rotate_session when token limit is exceeded."""
    cfg = Config(assistant={"system_prompt": "Bot.", "session_token_limit": 50})

    ai_msg = AIMessage(
        content="Hello!",
        response_metadata={"usage": {"total_tokens": 100}},
    )

    with patch(
        "graphbot.agent.nodes.llm_provider.achat",
        new_callable=AsyncMock,
        return_value=ai_msg,
    ), patch(
        "graphbot.agent.runner.asummarize",
        new_callable=AsyncMock,
        return_value="Summary of conversation.",
    ) as mock_sum, patch(
        "graphbot.agent.runner.aextract_facts",
        new_callable=AsyncMock,
        return_value={"notes": ["test fact"]},
    ) as mock_ext:
        runner = GraphRunner(cfg, store)
        response, sid = await runner.process("u1", "api", "hi")

    mock_sum.assert_called_once()
    mock_ext.assert_called_once()

    session = store.get_session(sid)
    assert session["ended_at"] is not None
    assert session["summary"] == "Summary of conversation."

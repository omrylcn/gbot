"""End-to-end tests — API → GraphRunner → LangGraph → tools → SQLite."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from graphbot.api.app import create_app
from graphbot.core.config import Config
from graphbot.memory.store import MemoryStore


@pytest.fixture
def app(tmp_path):
    config = Config(
        assistant={"system_prompt": "You are TestBot."},
        database={"path": str(tmp_path / "e2e.db")},
    )
    application = create_app()
    db = MemoryStore(str(tmp_path / "e2e.db"))
    from graphbot.agent.runner import GraphRunner

    ai_msg = AIMessage(
        content="ok",
        response_metadata={"usage": {"total_tokens": 10}},
    )
    with patch(
        "graphbot.agent.nodes.llm_provider.achat",
        new_callable=AsyncMock,
        return_value=ai_msg,
    ):
        runner = GraphRunner(config, db)

    application.state.config = config
    application.state.db = db
    application.state.runner = runner
    return application


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ── E2E: Tool call flow ────────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_chat_with_tool_call(client, app):
    """API → Runner → LLM (tool_call) → execute_tools → LLM (final) → SQLite."""
    # First LLM call returns a tool call, second returns final text
    tool_call_msg = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "tc1",
                "name": "save_user_note",
                "args": {"user_id": "u1", "note": "likes coffee"},
            }
        ],
        response_metadata={"usage": {"total_tokens": 30}},
    )
    final_msg = AIMessage(
        content="Not kaydedildi!",
        response_metadata={"usage": {"total_tokens": 40}},
    )

    with patch(
        "graphbot.agent.nodes.llm_provider.achat",
        new_callable=AsyncMock,
        side_effect=[tool_call_msg, final_msg],
    ):
        resp = await client.post(
            "/chat", json={"user_id": "u1", "message": "kahve seviyorum"}
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["response"] == "Not kaydedildi!"
    assert data["session_id"]

    # Verify tool actually wrote to SQLite
    db = app.state.db
    notes = db.get_notes("u1")
    assert any("coffee" in n for n in notes)

    # Verify messages persisted
    msgs = db.get_session_messages(data["session_id"])
    roles = [m["role"] for m in msgs]
    assert "user" in roles
    assert "assistant" in roles


# ── E2E: Session continuity ────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_session_continuity(client):
    """First message creates session, second message reuses it."""
    ai_msg = AIMessage(
        content="Merhaba!",
        response_metadata={"usage": {"total_tokens": 20}},
    )

    with patch(
        "graphbot.agent.nodes.llm_provider.achat",
        new_callable=AsyncMock,
        return_value=ai_msg,
    ):
        # First message — no session_id
        resp1 = await client.post(
            "/chat", json={"user_id": "u1", "message": "selam"}
        )
        sid = resp1.json()["session_id"]

        # Second message — pass session_id
        resp2 = await client.post(
            "/chat",
            json={"user_id": "u1", "message": "nasılsın?", "session_id": sid},
        )

    assert resp2.json()["session_id"] == sid

    # Verify both messages in same session
    resp = await client.get(f"/session/{sid}/history")
    messages = resp.json()["messages"]
    user_msgs = [m for m in messages if m["role"] == "user"]
    assert len(user_msgs) == 2


# ── E2E: Multi-user isolation ──────────────────────────────


@pytest.mark.asyncio
async def test_e2e_multi_user(client, app):
    """Two users get separate sessions, don't interfere."""
    ai_msg = AIMessage(
        content="Hello!",
        response_metadata={"usage": {"total_tokens": 15}},
    )

    with patch(
        "graphbot.agent.nodes.llm_provider.achat",
        new_callable=AsyncMock,
        return_value=ai_msg,
    ):
        resp1 = await client.post(
            "/chat", json={"user_id": "alice", "message": "hi"}
        )
        resp2 = await client.post(
            "/chat", json={"user_id": "bob", "message": "hey"}
        )

    sid1 = resp1.json()["session_id"]
    sid2 = resp2.json()["session_id"]
    assert sid1 != sid2

    # Each session has only its own user's messages
    db = app.state.db
    msgs1 = db.get_session_messages(sid1)
    msgs2 = db.get_session_messages(sid2)
    assert all(m["role"] in ("user", "assistant", "tool") for m in msgs1)
    assert all(m["role"] in ("user", "assistant", "tool") for m in msgs2)

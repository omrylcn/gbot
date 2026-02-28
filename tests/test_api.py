"""Tests for graphbot.api (Faz 4)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from graphbot.api.app import create_app
from graphbot.core.config import Config
from graphbot.memory.store import MemoryStore


@pytest.fixture
def app(tmp_path):
    """Create test app with tmp database and mocked LLM."""
    config = Config(
        assistant={
            "system_prompt": "You are TestBot.",
            "owner": {"username": "u1", "name": "Test Owner"},
        },
        database={"path": str(tmp_path / "test.db")},
    )
    application = create_app()
    # Override lifespan state manually
    db = MemoryStore(str(tmp_path / "test.db"))
    from graphbot.agent.runner import GraphRunner

    ai_msg = AIMessage(
        content="Test response",
        response_metadata={"usage": {"total_tokens": 50}},
    )
    with patch("graphbot.agent.nodes.llm_provider.achat", new_callable=AsyncMock, return_value=ai_msg):
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


# --- Health ---

@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# --- Auth ---

@pytest.mark.asyncio
async def test_register_login(client):
    # Register
    resp = await client.post(
        "/auth/register",
        json={"user_id": "u1", "password": "pass", "name": "Alice"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["user_id"] == "u1"

    # Duplicate
    resp = await client.post(
        "/auth/register",
        json={"user_id": "u1", "password": "pass", "name": "Alice"},
    )
    assert resp.json()["success"] is False

    # Login
    resp = await client.post(
        "/auth/login", json={"user_id": "u1", "password": "pass"}
    )
    assert resp.json()["success"] is True

    # Login unknown → 401
    resp = await client.post(
        "/auth/login", json={"user_id": "nope", "password": "pass"}
    )
    assert resp.status_code == 401


# --- Chat ---

@pytest.mark.asyncio
async def test_chat(client):
    ai_msg = AIMessage(
        content="Merhaba!",
        response_metadata={"usage": {"total_tokens": 50}},
    )
    with patch("graphbot.agent.nodes.llm_provider.achat", new_callable=AsyncMock, return_value=ai_msg):
        resp = await client.post(
            "/chat", json={"user_id": "u1", "message": "selam"}
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["response"] == "Merhaba!"
    assert data["session_id"]  # session_id returned


@pytest.mark.asyncio
async def test_chat_with_session(client):
    """Second message uses same session_id."""
    ai_msg = AIMessage(
        content="Reply",
        response_metadata={"usage": {"total_tokens": 50}},
    )
    with patch("graphbot.agent.nodes.llm_provider.achat", new_callable=AsyncMock, return_value=ai_msg):
        # First message — get session_id
        resp1 = await client.post("/chat", json={"user_id": "u1", "message": "hi"})
        sid = resp1.json()["session_id"]

        # Second message — pass session_id
        resp2 = await client.post(
            "/chat", json={"user_id": "u1", "message": "hello again", "session_id": sid}
        )
    assert resp2.json()["session_id"] == sid


# --- Sessions ---

@pytest.mark.asyncio
async def test_sessions_list(client):
    ai_msg = AIMessage(content="ok", response_metadata={"usage": {"total_tokens": 10}})
    with patch("graphbot.agent.nodes.llm_provider.achat", new_callable=AsyncMock, return_value=ai_msg):
        await client.post("/chat", json={"user_id": "u1", "message": "hi"})

    resp = await client.get("/sessions/u1")
    assert resp.status_code == 200
    sessions = resp.json()
    assert len(sessions) >= 1


@pytest.mark.asyncio
async def test_session_history(client):
    ai_msg = AIMessage(content="ok", response_metadata={"usage": {"total_tokens": 10}})
    with patch("graphbot.agent.nodes.llm_provider.achat", new_callable=AsyncMock, return_value=ai_msg):
        resp = await client.post("/chat", json={"user_id": "u1", "message": "hi"})
    sid = resp.json()["session_id"]

    resp = await client.get(f"/session/{sid}/history")
    assert resp.status_code == 200
    assert len(resp.json()["messages"]) >= 1


@pytest.mark.asyncio
async def test_session_end(client):
    ai_msg = AIMessage(content="ok", response_metadata={"usage": {"total_tokens": 10}})
    with patch("graphbot.agent.nodes.llm_provider.achat", new_callable=AsyncMock, return_value=ai_msg):
        resp = await client.post("/chat", json={"user_id": "u1", "message": "hi"})
    sid = resp.json()["session_id"]

    resp = await client.post(f"/session/{sid}/end")
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"

    # Double close
    resp = await client.post(f"/session/{sid}/end")
    assert resp.status_code == 400


# --- User Context ---

@pytest.mark.asyncio
async def test_user_context(client, app):
    app.state.db.add_note("u1", "likes coffee")
    resp = await client.get("/user/u1/context")
    assert resp.status_code == 200
    assert "coffee" in resp.json()["context_text"]


# --- User Profile ---

@pytest.mark.asyncio
async def test_user_profile(client, app):
    app.state.db.get_or_create_user("u1", name="Alice")
    resp = await client.get("/auth/user/u1")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Alice"

    resp = await client.get("/auth/user/unknown")
    assert resp.status_code == 404

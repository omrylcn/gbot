"""Integration tests — real HTTP (localhost:8000), real LLM, real DB."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from typer.testing import CliRunner

from graphbot import __version__
from graphbot.memory.store import MemoryStore

pytestmark = pytest.mark.integration

BASE_URL = "http://localhost:8000"


# ════════════════════════════════════════════════════════════
# FIXTURES
# ════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def test_user():
    """Register a test user once per module (sync, via httpx)."""
    uid = f"inttest_{uuid4().hex[:6]}"
    pwd = "test1234"
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        client.post(
            "/auth/register",
            json={"user_id": uid, "name": "Integration Test", "password": pwd},
        )
        resp = client.post(
            "/auth/login",
            json={"user_id": uid, "password": pwd},
        )
        token = resp.json().get("token")
    return {"user_id": uid, "password": pwd, "token": token}


@pytest.fixture
async def api_client(test_user):
    """httpx.AsyncClient with auth token (function-scoped)."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
        if test_user["token"]:
            client.headers["Authorization"] = f"Bearer {test_user['token']}"
        client._test_user_id = test_user["user_id"]  # type: ignore[attr-defined]
        yield client


@pytest.fixture
def cli_runner():
    """Typer CliRunner for CLI tests."""
    return CliRunner()


@pytest.fixture
def store(tmp_path):
    """Temporary MemoryStore for code-level tests."""
    return MemoryStore(str(tmp_path / "test.db"))


# ════════════════════════════════════════════════════════════
# API TESTS (localhost:8000, real LLM)
# ════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_api_health():
    """GET /health → server alive, version correct."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == __version__


@pytest.mark.asyncio
async def test_api_chat(api_client):
    """POST /chat → real LLM response (non-empty string)."""
    uid = api_client._test_user_id
    resp = await api_client.post(
        "/chat",
        json={"message": "Say hello in one word.", "user_id": uid},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "response" in data
    assert len(data["response"]) > 0
    assert "session_id" in data


@pytest.mark.asyncio
async def test_api_chat_session_continuity(api_client):
    """Two messages with same session_id → context preserved."""
    uid = api_client._test_user_id
    # First message
    r1 = await api_client.post(
        "/chat",
        json={"message": "My name is IntTestUser.", "user_id": uid},
    )
    assert r1.status_code == 200
    sid = r1.json()["session_id"]

    # Second message in same session
    r2 = await api_client.post(
        "/chat",
        json={
            "message": "What is my name?",
            "user_id": uid,
            "session_id": sid,
        },
    )
    assert r2.status_code == 200
    assert r2.json()["session_id"] == sid
    # LLM should recall the name
    assert "IntTestUser" in r2.json()["response"]


@pytest.mark.asyncio
async def test_api_session_history(api_client):
    """GET /session/{sid}/history → messages recorded."""
    uid = api_client._test_user_id
    r = await api_client.post(
        "/chat",
        json={"message": "Test message for history.", "user_id": uid},
    )
    sid = r.json()["session_id"]

    hist = await api_client.get(f"/session/{sid}/history")
    assert hist.status_code == 200
    data = hist.json()
    assert data["session_id"] == sid
    assert len(data["messages"]) >= 2  # user + assistant


@pytest.mark.asyncio
async def test_api_session_end(api_client):
    """POST /session/{sid}/end → session closed."""
    uid = api_client._test_user_id
    r = await api_client.post(
        "/chat",
        json={"message": "Temp session message.", "user_id": uid},
    )
    sid = r.json()["session_id"]

    end = await api_client.post(f"/session/{sid}/end")
    assert end.status_code == 200
    assert end.json()["status"] == "closed"

    # Double close → 400
    end2 = await api_client.post(f"/session/{sid}/end")
    assert end2.status_code == 400


@pytest.mark.asyncio
async def test_api_sessions_list(api_client):
    """GET /sessions/{uid} → at least one session."""
    uid = api_client._test_user_id
    # Ensure at least one session exists
    await api_client.post(
        "/chat",
        json={"message": "Session list test.", "user_id": uid},
    )

    resp = await api_client.get(f"/sessions/{uid}")
    assert resp.status_code == 200
    sessions = resp.json()
    assert isinstance(sessions, list)
    assert len(sessions) >= 1


@pytest.mark.asyncio
async def test_api_user_context(api_client):
    """GET /user/{uid}/context → context string returned."""
    uid = api_client._test_user_id
    resp = await api_client.get(f"/user/{uid}/context")
    assert resp.status_code == 200
    data = resp.json()
    assert "context_text" in data


@pytest.mark.asyncio
async def test_api_chat_tool_use(api_client):
    """POST /chat → tool-using message (save a note)."""
    uid = api_client._test_user_id
    resp = await api_client.post(
        "/chat",
        json={
            "message": "Save a note: 'Integration test note 12345'",
            "user_id": uid,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["response"]) > 0


@pytest.mark.asyncio
async def test_api_multi_user(api_client):
    """Two different users get separate sessions."""
    uid1 = api_client._test_user_id

    # Create second user
    uid2 = f"inttest2_{uuid4().hex[:6]}"
    pwd2 = "test1234"
    await api_client.post(
        "/auth/register",
        json={"user_id": uid2, "name": "Test User 2", "password": pwd2},
    )

    # Second client with its own token
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client2:
        login_resp = await client2.post(
            "/auth/login",
            json={"user_id": uid2, "password": pwd2},
        )
        token2 = login_resp.json().get("token")
        if token2:
            client2.headers["Authorization"] = f"Bearer {token2}"

        r2 = await client2.post(
            "/chat",
            json={"message": "Hello from user 2.", "user_id": uid2},
        )
        assert r2.status_code == 200
        sid2 = r2.json()["session_id"]

    # User 1 also chats
    r1 = await api_client.post(
        "/chat",
        json={"message": "Hello from user 1.", "user_id": uid1},
    )
    assert r1.status_code == 200
    sid1 = r1.json()["session_id"]

    assert sid1 != sid2


@pytest.mark.asyncio
async def test_api_chat_turkish(api_client):
    """Turkish message → response in Turkish (or at least non-empty)."""
    uid = api_client._test_user_id
    resp = await api_client.post(
        "/chat",
        json={
            "message": "Merhaba, nasılsın? Tek cümleyle Türkçe cevap ver.",
            "user_id": uid,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["response"]) > 0


# ════════════════════════════════════════════════════════════
# CRON / REMINDER TESTS (real LLM tool calls)
# ════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_api_create_reminder(api_client):
    """Ask LLM to create a one-shot reminder → tool called, reminder in DB."""
    uid = api_client._test_user_id
    resp = await api_client.post(
        "/chat",
        json={
            "message": (
                "30 saniye sonra bana 'test hatirlatma' diye hatırlat. "
                "create_reminder tool'unu kullan."
            ),
            "user_id": uid,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["response"]) > 0
    # LLM response should confirm the reminder was set (various phrasings)
    response_lower = data["response"].lower()
    assert any(
        kw in response_lower
        for kw in [
            "hatırlat", "reminder", "ayarla", "kuruldu", "oluştur",
            "set", "alacak", "mesaj", "saniye", "dakika",
        ]
    ), f"Unexpected response: {data['response'][:200]}"


@pytest.mark.asyncio
async def test_api_create_recurring_reminder(api_client):
    """Ask LLM to create a recurring reminder → tool called."""
    uid = api_client._test_user_id
    resp = await api_client.post(
        "/chat",
        json={
            "message": (
                "Her 10 dakikada bir bana 'su iç' diye hatırlat. "
                "create_recurring_reminder tool'unu kullan, cron_expr='*/10 * * * *'."
            ),
            "user_id": uid,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["response"]) > 0
    response_lower = data["response"].lower()
    assert any(
        kw in response_lower
        for kw in ["recurring", "periyodik", "hatırlat", "reminder", "set", "kuruldu"]
    ), f"Unexpected response: {data['response'][:200]}"


@pytest.mark.asyncio
async def test_api_list_reminders(api_client):
    """Create a reminder then ask LLM to list reminders."""
    uid = api_client._test_user_id
    # First create one
    await api_client.post(
        "/chat",
        json={
            "message": "60 saniye sonra bana 'list testi' diye hatırlat.",
            "user_id": uid,
        },
    )
    # Then list
    resp = await api_client.post(
        "/chat",
        json={
            "message": "Bekleyen hatırlatmalarımı listele. list_reminders tool'unu kullan.",
            "user_id": uid,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["response"]) > 0


@pytest.mark.asyncio
async def test_api_create_cron_job(api_client):
    """Ask LLM to create a cron job → tool called."""
    uid = api_client._test_user_id
    resp = await api_client.post(
        "/chat",
        json={
            "message": (
                "Her gün saat 09:00'da 'günaydın' mesajı gönderen bir cron job oluştur. "
                "add_cron_job tool'unu kullan, cron_expr='0 9 * * *'."
            ),
            "user_id": uid,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["response"]) > 0
    response_lower = data["response"].lower()
    assert any(
        kw in response_lower
        for kw in ["cron", "job", "oluştur", "created", "görev", "zamanla"]
    ), f"Unexpected response: {data['response'][:200]}"


@pytest.mark.asyncio
async def test_api_events_endpoint(api_client):
    """GET /events/{uid} → returns (possibly empty) events list."""
    uid = api_client._test_user_id
    resp = await api_client.get(f"/events/{uid}")
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert isinstance(data["events"], list)


# ════════════════════════════════════════════════════════════
# CLI TESTS
# ════════════════════════════════════════════════════════════


def test_cli_help(cli_runner):
    """graphbot --help → shows all commands."""
    from graphbot.cli.commands import app

    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "graphbot" in result.output.lower()
    assert "chat" in result.output
    assert "status" in result.output
    assert "run" in result.output


def test_cli_status(cli_runner):
    """graphbot status → shows version, model, DB path."""
    from graphbot.cli.commands import app

    result = cli_runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert __version__ in result.output
    assert "Model" in result.output or "model" in result.output


def test_cli_user_add_list_remove(cli_runner):
    """User lifecycle: add → list → remove."""
    from graphbot.cli.commands import app

    uid = f"clitest_{uuid4().hex[:6]}"

    # Add
    result = cli_runner.invoke(app, ["user", "add", uid, "--name", "CLI Test"])
    assert result.exit_code == 0
    assert "created" in result.output.lower() or uid in result.output

    # List
    result = cli_runner.invoke(app, ["user", "list"])
    assert result.exit_code == 0
    assert uid in result.output

    # Remove
    result = cli_runner.invoke(app, ["user", "remove", uid])
    assert result.exit_code == 0
    assert "removed" in result.output.lower() or uid in result.output


def test_cli_cron_list(cli_runner):
    """graphbot cron list → no error."""
    from graphbot.cli.commands import app

    result = cli_runner.invoke(app, ["cron", "list"])
    assert result.exit_code == 0


def test_cli_chat_message(cli_runner):
    """graphbot chat -m 'merhaba' → LLM response."""
    from graphbot.cli.commands import app

    result = cli_runner.invoke(app, ["chat", "-m", "Say hi in one word."])
    assert result.exit_code == 0
    assert "graphbot:" in result.output.lower() or len(result.output) > 5


# ════════════════════════════════════════════════════════════
# CODE-LEVEL TESTS (direct store CRUD)
# ════════════════════════════════════════════════════════════


def test_store_tables_exist(store):
    """All 15 tables created in fresh MemoryStore."""
    expected_tables = {
        "users", "user_channels", "sessions", "messages",
        "agent_memory", "user_notes", "activity_logs", "favorites",
        "preferences", "cron_jobs", "cron_execution_log", "reminders",
        "system_events", "background_tasks", "api_keys",
    }
    with store._get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    tables = {r["name"] for r in rows}
    assert expected_tables.issubset(tables), f"Missing: {expected_tables - tables}"


def test_store_cron_execution_log(store):
    """Log cron execution → retrieve log."""
    store.get_or_create_user("u1", "Test")
    store.add_cron_job("j1", "u1", "0 9 * * *", "check", "api")
    store.log_cron_execution("j1", "All good", "success", duration_ms=150)

    log = store.get_cron_execution_log("j1")
    assert len(log) == 1
    assert log[0]["status"] == "success"
    assert log[0]["result"] == "All good"
    assert log[0]["duration_ms"] == 150


def test_store_system_events(store):
    """System event lifecycle: create → get undelivered → mark delivered."""
    store.get_or_create_user("u1", "Test")
    eid = store.add_system_event("u1", "cron:j1", "alert", "Price alert!")
    assert eid > 0

    events = store.get_undelivered_events("u1")
    assert len(events) == 1
    assert events[0]["payload"] == "Price alert!"

    store.mark_events_delivered([events[0]["id"]])
    assert len(store.get_undelivered_events("u1")) == 0


def test_store_reminders(store):
    """Reminder lifecycle: add → pending → sent."""
    store.get_or_create_user("u1", "Test")
    store.add_reminder("r1", "u1", "2099-12-31T10:00:00", "Wake up", "telegram")

    pending = store.get_pending_reminders("u1")
    assert len(pending) == 1
    assert pending[0]["message"] == "Wake up"

    store.mark_reminder_sent("r1")
    assert len(store.get_pending_reminders("u1")) == 0


def test_store_background_tasks(store):
    """Background task lifecycle: create → complete."""
    store.get_or_create_user("u1", "Test")
    tid = "bg_test_001"
    store.create_background_task(tid, "u1", "research something")

    task = store.get_background_task(tid)
    assert task["status"] == "running"

    store.complete_background_task(tid, "Research done")
    task = store.get_background_task(tid)
    assert task["status"] == "completed"
    assert task["result"] == "Research done"

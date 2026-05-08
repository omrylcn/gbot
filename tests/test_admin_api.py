"""Tests for gbot.api.admin (Faz 16)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from gbot.api.app import create_app
from gbot.core.config import Config
from gbot.memory.store import MemoryStore


@pytest.fixture
def app(tmp_path):
    """Create test app with tmp database."""
    config = Config(
        assistant={"system_prompt": "TestBot."},
        database={"path": str(tmp_path / "test.db")},
    )
    application = create_app()
    db = MemoryStore(str(tmp_path / "test.db"))
    from unittest.mock import MagicMock

    from gbot.agent.runner import GraphRunner

    runner = MagicMock(spec=GraphRunner)
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


@pytest.mark.asyncio
async def test_admin_status(client):
    """GET /admin/status returns version and counts."""
    resp = await client.get("/admin/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data
    assert "status" in data
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_admin_config(client):
    """GET /admin/config returns sanitized config."""
    resp = await client.get("/admin/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "model" in data
    assert "auth_enabled" in data


@pytest.mark.asyncio
async def test_admin_skills(client):
    """GET /admin/skills returns list."""
    resp = await client.get("/admin/skills")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_admin_users(client, app):
    """GET /admin/users returns user list."""
    app.state.db.get_or_create_user("alice", name="Alice")
    resp = await client.get("/admin/users")
    assert resp.status_code == 200
    users = resp.json()
    assert any(u["user_id"] == "alice" for u in users)


@pytest.mark.asyncio
async def test_admin_tasks(client):
    """GET /admin/tasks returns list."""
    resp = await client.get("/admin/tasks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_admin_remove_task(client, app):
    """DELETE /admin/tasks/{task_id} cancels task."""
    app.state.db.get_or_create_user("alice", name="Alice")
    app.state.db.add_cron_job("cron-1", "alice", "*/5 * * * *", "ping")
    resp = await client.delete("/admin/tasks/cron-1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_admin_logs(client):
    """GET /admin/logs returns activity logs."""
    resp = await client.get("/admin/logs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ── Faz 22D admin endpoints ─────────────────────────────────


@pytest.mark.asyncio
async def test_admin_memory_relations(client):
    """GET /admin/memory/{user_id}/relations returns the relations list."""
    resp = await client.get("/admin/memory/u1/relations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "u1"
    assert "relations" in body
    assert isinstance(body["relations"], list)


@pytest.mark.asyncio
async def test_admin_memory_entities(client):
    """GET /admin/memory/{user_id}/entities returns canonical entity counts."""
    resp = await client.get("/admin/memory/u1/entities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "u1"
    assert "entities" in body


@pytest.mark.asyncio
async def test_admin_memory_entity_pages(client):
    """GET /admin/memory/{user_id}/entity-pages returns the pages list."""
    resp = await client.get("/admin/memory/u1/entity-pages")
    assert resp.status_code == 200
    body = resp.json()
    assert "pages" in body
    assert isinstance(body["pages"], list)


@pytest.mark.asyncio
async def test_admin_recompile_disabled_when_flag_off(client):
    """POST /admin/memory/{user_id}/pages/recompile returns ok=false when
    entity_pages.enabled is false (the default in tests).
    """
    resp = await client.post("/admin/memory/u1/pages/recompile?entity=Murat")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "entity_pages" in body.get("reason", "")


@pytest.mark.asyncio
async def test_admin_run_maintenance(client):
    """POST /admin/memory/{user_id}/maintenance/run returns daily+weekly stats."""
    resp = await client.post("/admin/memory/u1/maintenance/run")
    assert resp.status_code == 200
    body = resp.json()
    assert "daily" in body
    assert "weekly" in body
    assert body["daily"]["kind"] == "daily"
    assert body["weekly"]["kind"] == "weekly"


@pytest.mark.asyncio
async def test_admin_forget_entity(client):
    """DELETE /admin/memory/{user_id}/entity/{entity} returns archive counts."""
    resp = await client.delete("/admin/memory/u1/entity/SomeoneNeverHeardOf")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entity"] == "SomeoneNeverHeardOf"
    assert "archived" in body
    # Unknown entity → 0 across the board
    assert body["archived"]["relations"] == 0
    assert body["archived"]["facts"] == 0
    assert body["archived"]["pages"] == 0

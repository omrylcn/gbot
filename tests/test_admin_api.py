"""Tests for graphbot.api.admin (Faz 16)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from graphbot.api.app import create_app
from graphbot.core.config import Config
from graphbot.memory.store import MemoryStore


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

    from graphbot.agent.runner import GraphRunner

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
async def test_admin_crons(client):
    """GET /admin/crons returns empty list."""
    resp = await client.get("/admin/crons")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_admin_remove_cron(client, app):
    """DELETE /admin/crons/{job_id} removes job."""
    app.state.db.get_or_create_user("alice", name="Alice")
    app.state.db.add_cron_job("cron-1", "alice", "*/5 * * * *", "ping")
    resp = await client.delete("/admin/crons/cron-1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"


@pytest.mark.asyncio
async def test_admin_logs(client):
    """GET /admin/logs returns activity logs."""
    resp = await client.get("/admin/logs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

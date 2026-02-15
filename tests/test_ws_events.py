"""Tests for WebSocket event delivery + ConnectionManager."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphbot.api.ws import ConnectionManager
from graphbot.core.background.worker import SubagentWorker
from graphbot.core.config import Config
from graphbot.core.cron.scheduler import CronScheduler
from graphbot.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(str(tmp_path / "test.db"))
    s.get_or_create_user("u1", "Test User")
    return s


@pytest.fixture
def mock_runner():
    runner = AsyncMock()
    runner.process = AsyncMock(return_value=("Done", "sess-1"))
    return runner


@pytest.fixture
def cfg():
    return Config()


@pytest.fixture
def manager():
    return ConnectionManager()


# ── ConnectionManager ────────────────────────────────────────


def test_manager_connect_disconnect(manager):
    """Connect and disconnect tracking."""
    ws = MagicMock()
    manager.connect("u1", ws)
    assert manager.is_connected("u1") is True

    manager.disconnect("u1", ws)
    assert manager.is_connected("u1") is False


def test_manager_multiple_connections(manager):
    """Multiple connections per user (multiple tabs)."""
    ws1, ws2 = MagicMock(), MagicMock()
    manager.connect("u1", ws1)
    manager.connect("u1", ws2)
    assert manager.is_connected("u1") is True

    manager.disconnect("u1", ws1)
    assert manager.is_connected("u1") is True

    manager.disconnect("u1", ws2)
    assert manager.is_connected("u1") is False


@pytest.mark.asyncio
async def test_manager_send_event(manager):
    """Send event to connected user."""
    ws = AsyncMock()
    manager.connect("u1", ws)

    event = {"type": "event", "event_type": "test", "payload": "hello"}
    result = await manager.send_event("u1", event)
    assert result is True
    ws.send_json.assert_called_once_with(event)


@pytest.mark.asyncio
async def test_manager_send_no_connection(manager):
    """Send to disconnected user returns False."""
    result = await manager.send_event("u1", {"type": "event"})
    assert result is False


@pytest.mark.asyncio
async def test_manager_send_cleans_broken(manager):
    """Broken connections are cleaned up during send."""
    ws = AsyncMock()
    ws.send_json.side_effect = Exception("connection closed")
    manager.connect("u1", ws)

    result = await manager.send_event("u1", {"type": "event"})
    assert result is False
    assert manager.is_connected("u1") is False


# ── CronScheduler WS push ──────────────────────────────────


@pytest.mark.asyncio
async def test_cron_send_ws_push(store, mock_runner, manager):
    """API channel + connected WS -> push via WS."""
    ws = AsyncMock()
    manager.connect("u1", ws)

    sched = CronScheduler(store, mock_runner)
    sched.ws_manager = manager

    result = await sched._send_to_channel("u1", "api", "Hello from cron")
    assert result is True
    ws.send_json.assert_called_once()
    sent = ws.send_json.call_args[0][0]
    assert sent["type"] == "event"
    assert sent["payload"] == "Hello from cron"


@pytest.mark.asyncio
async def test_cron_send_ws_fallback(store, mock_runner, manager):
    """API channel + no WS -> fallback to system_event in DB."""
    sched = CronScheduler(store, mock_runner)
    sched.ws_manager = manager

    result = await sched._send_to_channel("u1", "api", "Hello from cron")
    assert result is False

    events = store.get_undelivered_events("u1")
    assert len(events) == 1
    assert events[0]["payload"] == "Hello from cron"


@pytest.mark.asyncio
async def test_cron_telegram_unchanged(store, mock_runner, manager):
    """Telegram channel path is not affected by WS manager."""
    sched = CronScheduler(store, mock_runner)
    sched.ws_manager = manager

    # No telegram link -> returns False (existing behavior)
    result = await sched._send_to_channel("u1", "telegram", "test")
    assert result is False
    # Should NOT create system_event for telegram
    assert len(store.get_undelivered_events("u1")) == 0


# ── SubagentWorker WS push ──────────────────────────────────


@pytest.mark.asyncio
async def test_worker_ws_push(store, cfg, manager):
    """Worker pushes via WS and marks event delivered."""
    ws = AsyncMock()
    manager.connect("u1", ws)

    worker = SubagentWorker(cfg, db=store)
    worker.ws_manager = manager

    with patch("graphbot.agent.light.LightAgent") as MockAgent:
        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=("Research done", 100))
        MockAgent.return_value = mock_agent

        worker.spawn("u1", "research", "api")
        await asyncio.sleep(0.3)

    ws.send_json.assert_called()
    sent = ws.send_json.call_args[0][0]
    assert sent["type"] == "event"
    assert sent["event_type"] == "task_completed"

    # Event should be marked delivered
    events = store.get_undelivered_events("u1")
    assert len(events) == 0


@pytest.mark.asyncio
async def test_worker_no_ws_fallback(store, cfg):
    """Worker without WS manager -> event stays undelivered in DB."""
    worker = SubagentWorker(cfg, db=store)

    with patch("graphbot.agent.light.LightAgent") as MockAgent:
        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=("Research done", 100))
        MockAgent.return_value = mock_agent

        worker.spawn("u1", "research", "api")
        await asyncio.sleep(0.3)

    events = store.get_undelivered_events("u1")
    assert len(events) == 1
    assert events[0]["event_type"] == "task_completed"

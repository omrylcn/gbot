"""Tests for Faz 7 — Channel Entegrasyonu (Telegram + Altyapı)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from graphbot.core.channels.base import check_allowlist, resolve_or_create_user
from graphbot.core.channels.telegram import md_to_html
from graphbot.core.config import Config
from graphbot.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(str(tmp_path / "test.db"))


@pytest.fixture
def cfg():
    return Config(
        channels={"telegram": {"enabled": True, "token": "fake-token", "allow_from": ["111", "222"]}}
    )


# ── Cross-Channel Identity ────────────────────────────────


def test_resolve_or_create_user_new(store):
    """New channel user → new user_id created and linked."""
    user_id = resolve_or_create_user(store, "telegram", "12345")
    assert user_id == "telegram_12345"
    assert store.user_exists(user_id)
    # Resolve again → same user
    assert store.resolve_user("telegram", "12345") == "telegram_12345"


def test_resolve_or_create_user_existing(store):
    """Existing linked user → returns existing user_id."""
    store.get_or_create_user("my_user")
    store.link_channel("my_user", "telegram", "99999")

    user_id = resolve_or_create_user(store, "telegram", "99999")
    assert user_id == "my_user"


# ── Allowlist ──────────────────────────────────────────────


def test_check_allowlist_empty():
    """Empty allow_from list → allow everyone."""
    cfg = Config(channels={"telegram": {"enabled": True, "allow_from": []}})
    assert check_allowlist(cfg.channels, "telegram", "anyone") is True


def test_check_allowlist_allowed(cfg):
    """Sender in list → allowed."""
    assert check_allowlist(cfg.channels, "telegram", "111") is True


def test_check_allowlist_denied(cfg):
    """Sender not in list → denied."""
    assert check_allowlist(cfg.channels, "telegram", "999") is False


def test_check_allowlist_unknown_channel(cfg):
    """Unknown channel → denied."""
    assert check_allowlist(cfg.channels, "unknown_channel", "111") is False


# ── Markdown to HTML ───────────────────────────────────────


def test_md_to_html_bold():
    assert "<b>bold</b>" in md_to_html("**bold**")


def test_md_to_html_italic():
    assert "<i>italic</i>" in md_to_html("*italic*")


def test_md_to_html_code():
    assert "<code>code</code>" in md_to_html("`code`")


def test_md_to_html_code_block():
    result = md_to_html("```python\nprint('hi')\n```")
    assert "<pre>" in result
    assert "print" in result


def test_md_to_html_link():
    result = md_to_html("[Google](https://google.com)")
    assert '<a href="https://google.com">Google</a>' in result


def test_md_to_html_escapes_html():
    result = md_to_html("1 < 2 & 3 > 0")
    assert "&lt;" in result
    assert "&amp;" in result
    assert "&gt;" in result


# ── Telegram Webhook Endpoint ──────────────────────────────


@pytest.fixture
def telegram_update():
    """Minimal Telegram Update object."""
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": 111, "first_name": "Test"},
            "chat": {"id": 111},
            "text": "Hello bot",
        },
    }


@pytest.mark.asyncio
async def test_telegram_webhook(telegram_update, tmp_path):
    """Valid update → runner.process called, response sent."""
    from graphbot.api.app import create_app

    app = create_app()

    mock_runner = AsyncMock()
    mock_runner.process = AsyncMock(return_value=("Hi there!", "sess-1"))

    app.state.config = Config(
        channels={"telegram": {"enabled": True, "token": "fake-token", "allow_from": []}}
    )
    app.state.db = MemoryStore(str(tmp_path / "tg.db"))
    app.state.runner = mock_runner

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("graphbot.core.channels.telegram.send_message", new_callable=AsyncMock) as mock_send:
            resp = await client.post("/webhooks/telegram", json=telegram_update)

    assert resp.status_code == 200
    mock_runner.process.assert_called_once()
    call_kwargs = mock_runner.process.call_args.kwargs
    assert call_kwargs["channel"] == "telegram"
    assert call_kwargs["message"] == "Hello bot"


@pytest.mark.asyncio
async def test_telegram_allowlist_denied(telegram_update, tmp_path):
    """Sender not in allowlist → 403."""
    from graphbot.api.app import create_app

    app = create_app()

    app.state.config = Config(
        channels={"telegram": {"enabled": True, "token": "fake-token", "allow_from": ["999"]}}
    )
    app.state.db = MemoryStore(str(tmp_path / "tg.db"))
    app.state.runner = AsyncMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/telegram", json=telegram_update)

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_telegram_non_message_update(tmp_path):
    """Update without message → 200 OK, no processing."""
    from graphbot.api.app import create_app

    app = create_app()
    app.state.config = Config()
    app.state.db = MemoryStore(str(tmp_path / "tg.db"))
    app.state.runner = AsyncMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/telegram", json={"update_id": 1})

    assert resp.status_code == 200
    app.state.runner.process.assert_not_called()


# ── Stub Endpoints ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_stub_endpoints(tmp_path):
    """Discord, WhatsApp, Feishu stubs return 501."""
    from graphbot.api.app import create_app

    app = create_app()
    app.state.config = Config()
    app.state.db = MemoryStore(str(tmp_path / "tg.db"))
    app.state.runner = AsyncMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ["/webhooks/discord", "/webhooks/whatsapp", "/webhooks/feishu"]:
            resp = await client.post(path)
            assert resp.status_code == 501, f"{path} should return 501"

"""Tests for WhatsApp channel — WAHA integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from graphbot.core.channels.waha_client import WAHAClient
from graphbot.core.channels.whatsapp import split_message
from graphbot.core.config import Config
from graphbot.memory.store import MemoryStore


# ── WAHAClient helpers ────────────────────────────────────


def test_phone_to_chat_id():
    assert WAHAClient.phone_to_chat_id("905551234567") == "905551234567@c.us"


def test_phone_to_chat_id_with_plus():
    assert WAHAClient.phone_to_chat_id("+905551234567") == "905551234567@c.us"


def test_phone_to_chat_id_with_spaces():
    assert WAHAClient.phone_to_chat_id("+90 555 123 4567") == "905551234567@c.us"


def test_chat_id_to_phone():
    assert WAHAClient.chat_id_to_phone("905551234567@c.us") == "905551234567"


def test_chat_id_to_phone_no_suffix():
    assert WAHAClient.chat_id_to_phone("905551234567") == "905551234567"


# ── Message splitting ─────────────────────────────────────


def test_split_short_message():
    assert split_message("Hello") == ["Hello"]


def test_split_exact_limit():
    text = "a" * 4096
    assert split_message(text) == [text]


def test_split_long_message():
    chunks = split_message("a" * 5000, max_length=4096)
    assert len(chunks) >= 2
    assert all(len(c) <= 4096 for c in chunks)


def test_split_at_paragraph_boundary():
    para1 = "First paragraph. " * 100  # ~1700 chars
    para2 = "Second paragraph. " * 100
    para3 = "Third paragraph. " * 100
    text = f"{para1}\n\n{para2}\n\n{para3}"
    chunks = split_message(text, max_length=4096)
    assert len(chunks) >= 2
    # Content is preserved
    joined = "\n\n".join(chunks)
    assert "First paragraph" in joined
    assert "Third paragraph" in joined


def test_split_empty_message():
    assert split_message("") == [""]


# ── send_whatsapp_message prefix ─────────────────────────


@pytest.mark.asyncio
async def test_send_whatsapp_message_auto_prefix():
    """send_whatsapp_message auto-prefixes [gbot] if missing."""
    from graphbot.core.channels.whatsapp import send_whatsapp_message

    wa_config = type("C", (), {"waha_url": "http://x", "session": "s", "api_key": "k"})()
    with patch("graphbot.core.channels.whatsapp.WAHAClient") as MockClient:
        mock_instance = AsyncMock()
        MockClient.return_value = mock_instance
        await send_whatsapp_message(wa_config, "123@c.us", "hello")
        mock_instance.send_text.assert_called_once_with("123@c.us", "[gbot] hello")


@pytest.mark.asyncio
async def test_send_whatsapp_message_no_double_prefix():
    """send_whatsapp_message does not double-prefix [gbot]."""
    from graphbot.core.channels.whatsapp import send_whatsapp_message

    wa_config = type("C", (), {"waha_url": "http://x", "session": "s", "api_key": "k"})()
    with patch("graphbot.core.channels.whatsapp.WAHAClient") as MockClient:
        mock_instance = AsyncMock()
        MockClient.return_value = mock_instance
        await send_whatsapp_message(wa_config, "123@c.us", "[gbot] already prefixed")
        mock_instance.send_text.assert_called_once_with("123@c.us", "[gbot] already prefixed")


# ── Webhook fixtures ──────────────────────────────────────


TEST_GROUP_ID = "120363407143421687@g.us"


@pytest.fixture
def waha_group_message_event():
    """WAHA webhook message event from an allowed group."""
    return {
        "event": "message",
        "session": "default",
        "payload": {
            "id": "true_120363407143421687@g.us_AAAA",
            "from": TEST_GROUP_ID,
            "participant": "905551234567@c.us",
            "fromMe": False,
            "body": "gbot Merhaba bot!",
            "timestamp": 1700000000,
            "hasMedia": False,
        },
    }


@pytest.fixture
def waha_dm_event():
    """WAHA webhook DM event (should be ignored by default)."""
    return {
        "event": "message",
        "session": "default",
        "payload": {
            "id": "true_905551234567@c.us_AAAA",
            "from": "905551234567@c.us",
            "fromMe": False,
            "to": "905559876543@c.us",
            "body": "Merhaba bot!",
            "timestamp": 1700000000,
            "hasMedia": False,
        },
    }


def _make_app(tmp_path, *, config_overrides=None, users=None):
    """Helper to create a test app with db, config, and mock runner."""
    from graphbot.api.app import create_app

    app = create_app()
    mock_runner = AsyncMock()
    mock_runner.process = AsyncMock(return_value=("Cevap", "sess-1"))

    wa_config = {
        "enabled": True,
        "waha_url": "http://waha:3000",
        "allowed_groups": [TEST_GROUP_ID],
    }
    if config_overrides:
        wa_config.update(config_overrides)

    app.state.config = Config(channels={"whatsapp": wa_config})
    db = MemoryStore(str(tmp_path / "wa.db"))

    if users:
        for uid, kwargs in users.items():
            name = kwargs.get("name")
            db.get_or_create_user(uid, name=name)
            if "phone" in kwargs:
                db.link_channel(uid, "whatsapp", kwargs["phone"])

    app.state.db = db
    app.state.runner = mock_runner
    return app, db, mock_runner


# ── User-specific webhook: Group messages ─────────────────


@pytest.mark.asyncio
async def test_group_message_processed(waha_group_message_event, tmp_path):
    """Group message in allowed group → runner.process called."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "graphbot.core.channels.whatsapp.send_whatsapp_message",
            new_callable=AsyncMock,
        ):
            resp = await client.post(
                "/webhooks/whatsapp/testuser", json=waha_group_message_event
            )

    assert resp.status_code == 200
    mock_runner.process.assert_called_once()
    call_kwargs = mock_runner.process.call_args.kwargs
    assert call_kwargs["channel"] == "whatsapp"
    assert call_kwargs["message"] == "gbot Merhaba bot!"
    assert call_kwargs["user_id"] == "testuser"


@pytest.mark.asyncio
async def test_group_message_no_prefix_also_processed(tmp_path):
    """Group message without any prefix → still processed (like Telegram)."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )

    event = {
        "event": "message",
        "session": "default",
        "payload": {
            "from": TEST_GROUP_ID,
            "participant": "905551234567@c.us",
            "fromMe": False,
            "body": "selam herkese",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "graphbot.core.channels.whatsapp.send_whatsapp_message",
            new_callable=AsyncMock,
        ) as mock_send:
            resp = await client.post("/webhooks/whatsapp/testuser", json=event)

    assert resp.status_code == 200
    mock_runner.process.assert_called_once()
    assert mock_runner.process.call_args.kwargs["message"] == "selam herkese"
    # Response sent (prefix added inside send_whatsapp_message, not by caller)
    mock_send.assert_called_once()
    sent_text = mock_send.call_args.args[2]
    assert sent_text  # non-empty response


@pytest.mark.asyncio
async def test_group_response_has_gbot_prefix(tmp_path):
    """All group responses are prefixed with [gbot]."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )
    mock_runner.process = AsyncMock(return_value=("Hava güzel!", "sess-1"))

    event = {
        "event": "message",
        "session": "default",
        "payload": {
            "from": TEST_GROUP_ID,
            "participant": "905551234567@c.us",
            "fromMe": False,
            "body": "hava nasıl",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "graphbot.core.channels.whatsapp.send_whatsapp_message",
            new_callable=AsyncMock,
        ) as mock_send:
            resp = await client.post("/webhooks/whatsapp/testuser", json=event)

    assert resp.status_code == 200
    sent_text = mock_send.call_args.args[2]
    # Prefix added inside send_whatsapp_message (mocked here), caller sends raw
    assert sent_text == "Hava güzel!"


@pytest.mark.asyncio
async def test_gbot_loop_prevention(tmp_path):
    """Bot's own [gbot] prefixed messages (fromMe) are skipped to prevent loops."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )

    event = {
        "event": "message.any",
        "session": "default",
        "payload": {
            "from": TEST_GROUP_ID,
            "fromMe": True,
            "body": "[gbot] Hava güzel!",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/whatsapp/testuser", json=event)

    assert resp.status_code == 200
    mock_runner.process.assert_not_called()


@pytest.mark.asyncio
async def test_fromme_group_non_gbot_processed(tmp_path):
    """fromMe=True in group WITHOUT [gbot] prefix → processed (owner's command)."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )

    event = {
        "event": "message.any",
        "session": "default",
        "payload": {
            "from": TEST_GROUP_ID,
            "fromMe": True,
            "body": "hava nasıl",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "graphbot.core.channels.whatsapp.send_whatsapp_message",
            new_callable=AsyncMock,
        ):
            resp = await client.post("/webhooks/whatsapp/testuser", json=event)

    assert resp.status_code == 200
    mock_runner.process.assert_called_once()
    assert mock_runner.process.call_args.kwargs["message"] == "hava nasıl"


# ── User-specific webhook: DM handling ────────────────────


@pytest.mark.asyncio
async def test_dm_ignored_by_default(waha_dm_event, tmp_path):
    """DMs are ignored when both monitor_dm and respond_to_dm are false (default)."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/webhooks/whatsapp/testuser", json=waha_dm_event
        )

    assert resp.status_code == 200
    mock_runner.process.assert_not_called()
    # No session should be created
    session = db.get_active_session("testuser", channel="whatsapp")
    assert session is None


@pytest.mark.asyncio
async def test_dm_stored_when_monitor_enabled(waha_dm_event, tmp_path):
    """DM stored in whatsapp session when monitor_dm=true, but NOT responded to."""
    app, db, mock_runner = _make_app(
        tmp_path,
        config_overrides={"monitor_dm": True},
        users={
            "owner": {"phone": "905559876543"},
            "zynp": {"phone": "905551234567", "name": "Zeynep"},
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/whatsapp/owner", json=waha_dm_event)

    assert resp.status_code == 200
    mock_runner.process.assert_not_called()

    # Message should be stored in the owner's whatsapp session
    session = db.get_active_session("owner", channel="whatsapp")
    assert session is not None
    messages = db.get_session_messages(session["session_id"])
    assert len(messages) == 1
    assert "[WhatsApp DM]" in messages[0]["content"]
    assert "Zeynep" in messages[0]["content"]
    assert "Merhaba bot!" in messages[0]["content"]


@pytest.mark.asyncio
async def test_dm_isolated_from_telegram_session(waha_dm_event, tmp_path):
    """DM messages stay in whatsapp session — do not appear in telegram session."""
    app, db, mock_runner = _make_app(
        tmp_path,
        config_overrides={"monitor_dm": True},
        users={"owner": {"phone": "905559876543"}},
    )

    # Create a separate telegram session
    tg_sid = db.create_session("owner", channel="telegram")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/whatsapp/owner", json=waha_dm_event)

    assert resp.status_code == 200

    # Telegram session should have NO messages
    tg_messages = db.get_session_messages(tg_sid)
    assert len(tg_messages) == 0

    # WhatsApp session should have the DM
    wa_session = db.get_active_session("owner", channel="whatsapp")
    assert wa_session is not None
    wa_messages = db.get_session_messages(wa_session["session_id"])
    assert len(wa_messages) == 1
    assert "[WhatsApp DM]" in wa_messages[0]["content"]


@pytest.mark.asyncio
async def test_dm_respond_when_enabled(waha_dm_event, tmp_path):
    """respond_to_dm=true processes DM through runner but does NOT auto-send.

    LLM decides whether to reply via send_message_to_user tool.
    """
    app, db, mock_runner = _make_app(
        tmp_path,
        config_overrides={"respond_to_dm": True},
        users={
            "owner": {"phone": "905559876543"},
            "sender": {"phone": "905551234567"},
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "graphbot.core.channels.whatsapp.send_whatsapp_message",
            new_callable=AsyncMock,
        ) as mock_send:
            resp = await client.post("/webhooks/whatsapp/owner", json=waha_dm_event)

    assert resp.status_code == 200
    mock_runner.process.assert_called_once()
    # Response NOT auto-sent to DM sender — LLM uses tools if needed
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_dm_fromme_ignored(tmp_path):
    """fromMe=True in DM → always ignored."""
    app, db, mock_runner = _make_app(
        tmp_path,
        config_overrides={"respond_to_dm": True, "monitor_dm": True},
        users={"testuser": {"phone": "905551234567"}},
    )

    event = {
        "event": "message.any",
        "session": "default",
        "payload": {"from": "905551234567@c.us", "fromMe": True, "body": "my msg"},
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/whatsapp/testuser", json=event)

    assert resp.status_code == 200
    mock_runner.process.assert_not_called()


# ── Filtering: newsletters, broadcasts, non-allowed groups ─


@pytest.mark.asyncio
async def test_newsletter_ignored(tmp_path):
    """Newsletter messages (@newsletter) are ignored."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )

    event = {
        "event": "message",
        "session": "default",
        "payload": {
            "from": "120363100000000000@newsletter",
            "fromMe": False,
            "body": "Newsletter content",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/whatsapp/testuser", json=event)

    assert resp.status_code == 200
    mock_runner.process.assert_not_called()


@pytest.mark.asyncio
async def test_broadcast_ignored(tmp_path):
    """Broadcast messages (@broadcast) are ignored."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )

    event = {
        "event": "message",
        "session": "default",
        "payload": {
            "from": "status@broadcast",
            "fromMe": False,
            "body": "Status update",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/whatsapp/testuser", json=event)

    assert resp.status_code == 200
    mock_runner.process.assert_not_called()


@pytest.mark.asyncio
async def test_non_allowed_group_ignored(tmp_path):
    """Messages from groups not in allowed_groups are ignored."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )

    event = {
        "event": "message",
        "session": "default",
        "payload": {
            "from": "999999999999@g.us",
            "participant": "905551234567@c.us",
            "fromMe": False,
            "body": "Hello from other group",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/whatsapp/testuser", json=event)

    assert resp.status_code == 200
    mock_runner.process.assert_not_called()


@pytest.mark.asyncio
async def test_non_message_event_ignored(tmp_path):
    """Non-message event type → ignored."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )

    event = {"event": "session.status", "session": "default", "payload": {}}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/whatsapp/testuser", json=event)

    assert resp.status_code == 200
    mock_runner.process.assert_not_called()


@pytest.mark.asyncio
async def test_empty_body_ignored(tmp_path):
    """Messages with empty body are ignored."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )

    event = {
        "event": "message",
        "session": "default",
        "payload": {
            "from": TEST_GROUP_ID,
            "participant": "905551234567@c.us",
            "fromMe": False,
            "body": "",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/whatsapp/testuser", json=event)

    assert resp.status_code == 200
    mock_runner.process.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_user_returns_404(waha_group_message_event, tmp_path):
    """Unknown user_id in path → 404."""
    app, db, mock_runner = _make_app(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/webhooks/whatsapp/nobody", json=waha_group_message_event
        )

    assert resp.status_code == 404


# ── Duplicate event handling ──────────────────────────────


@pytest.mark.asyncio
async def test_message_any_non_fromme_ignored(tmp_path):
    """message.any event with fromMe=False → ignored (prevent duplicates)."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )

    event = {
        "event": "message.any",
        "session": "default",
        "payload": {
            "from": TEST_GROUP_ID,
            "participant": "905551234567@c.us",
            "fromMe": False,
            "body": "selam",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/whatsapp/testuser", json=event)

    assert resp.status_code == 200
    mock_runner.process.assert_not_called()


@pytest.mark.asyncio
async def test_message_any_fromme_processed(tmp_path):
    """message.any event with fromMe=True (owner's message) → processed."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )

    event = {
        "event": "message.any",
        "session": "default",
        "payload": {
            "from": TEST_GROUP_ID,
            "fromMe": True,
            "body": "hava nasıl",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "graphbot.core.channels.whatsapp.send_whatsapp_message",
            new_callable=AsyncMock,
        ):
            resp = await client.post("/webhooks/whatsapp/testuser", json=event)

    assert resp.status_code == 200
    mock_runner.process.assert_called_once()


# ── Session management ────────────────────────────────────


@pytest.mark.asyncio
async def test_group_message_creates_whatsapp_session(tmp_path):
    """First group message creates a whatsapp-isolated session."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )

    event = {
        "event": "message",
        "session": "default",
        "payload": {
            "from": TEST_GROUP_ID,
            "participant": "905551234567@c.us",
            "fromMe": False,
            "body": "merhaba",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "graphbot.core.channels.whatsapp.send_whatsapp_message",
            new_callable=AsyncMock,
        ):
            resp = await client.post("/webhooks/whatsapp/testuser", json=event)

    assert resp.status_code == 200
    session = db.get_active_session("testuser", channel="whatsapp")
    assert session is not None


@pytest.mark.asyncio
async def test_group_session_isolated_from_api(tmp_path):
    """WhatsApp group session is separate from API session."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"testuser": {"phone": "905551234567"}}
    )

    # Create an API session first
    api_sid = db.create_session("testuser", channel="api")

    event = {
        "event": "message",
        "session": "default",
        "payload": {
            "from": TEST_GROUP_ID,
            "participant": "905551234567@c.us",
            "fromMe": False,
            "body": "merhaba",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "graphbot.core.channels.whatsapp.send_whatsapp_message",
            new_callable=AsyncMock,
        ):
            resp = await client.post("/webhooks/whatsapp/testuser", json=event)

    assert resp.status_code == 200
    wa_session = db.get_active_session("testuser", channel="whatsapp")
    assert wa_session is not None
    assert wa_session["session_id"] != api_sid


# ── Global webhook ────────────────────────────────────────


@pytest.mark.asyncio
async def test_global_webhook_routes_group_by_phone(
    waha_group_message_event, tmp_path
):
    """Global webhook resolves phone → user_id from group participant."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"ali": {"phone": "905551234567"}}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "graphbot.core.channels.whatsapp.send_whatsapp_message",
            new_callable=AsyncMock,
        ):
            resp = await client.post(
                "/webhooks/whatsapp", json=waha_group_message_event
            )

    assert resp.status_code == 200
    mock_runner.process.assert_called_once()
    call_kwargs = mock_runner.process.call_args.kwargs
    assert call_kwargs["user_id"] == "ali"
    assert call_kwargs["channel"] == "whatsapp"


@pytest.mark.asyncio
async def test_global_webhook_ignores_dm(waha_dm_event, tmp_path):
    """Global webhook ignores DM messages entirely."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"owner": {"phone": "905551234567"}}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/whatsapp", json=waha_dm_event)

    assert resp.status_code == 200
    mock_runner.process.assert_not_called()


@pytest.mark.asyncio
async def test_global_webhook_ignores_newsletter(tmp_path):
    """Global webhook ignores newsletter messages."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"owner": {"phone": "905551234567"}}
    )

    event = {
        "event": "message",
        "session": "default",
        "payload": {
            "from": "120363100000000000@newsletter",
            "fromMe": False,
            "body": "Newsletter update",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/whatsapp", json=event)

    assert resp.status_code == 200
    mock_runner.process.assert_not_called()


@pytest.mark.asyncio
async def test_global_webhook_ignores_non_allowed_group(tmp_path):
    """Global webhook ignores groups not in allowed_groups."""
    app, db, mock_runner = _make_app(
        tmp_path, users={"owner": {"phone": "905551234567"}}
    )

    event = {
        "event": "message",
        "session": "default",
        "payload": {
            "from": "999999999999@g.us",
            "participant": "905551234567@c.us",
            "fromMe": False,
            "body": "Hello",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/whatsapp", json=event)

    assert resp.status_code == 200
    mock_runner.process.assert_not_called()


@pytest.mark.asyncio
async def test_global_webhook_unknown_sender(tmp_path):
    """Unknown sender phone in group → 200 OK but no processing."""
    app, db, mock_runner = _make_app(tmp_path)

    event = {
        "event": "message",
        "session": "default",
        "payload": {
            "from": TEST_GROUP_ID,
            "participant": "905559999999@c.us",
            "fromMe": False,
            "body": "selam",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhooks/whatsapp", json=event)

    assert resp.status_code == 200
    mock_runner.process.assert_not_called()

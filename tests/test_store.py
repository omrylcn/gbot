"""Tests for graphbot.memory.store."""

import pytest
from graphbot.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(str(tmp_path / "test.db"))


def test_user_crud(store):
    store.get_or_create_user("u1", name="Alice")
    assert store.user_exists("u1")
    assert store.get_user("u1")["name"] == "Alice"
    assert not store.user_exists("nope")


def test_user_channels(store):
    store.link_channel("u1", "telegram", "12345")
    store.link_channel("u1", "discord", "omrylcn")
    assert store.resolve_user("telegram", "12345") == "u1"
    assert store.resolve_user("discord", "omrylcn") == "u1"
    assert store.resolve_user("telegram", "unknown") is None


def test_sessions(store):
    sid = store.create_session("u1", channel="api")
    assert store.get_active_session("u1")["session_id"] == sid

    store.update_session_token_count(sid, 15000)
    store.end_session(sid, summary="test", close_reason="token_limit")

    s = store.get_session(sid)
    assert s["token_count"] == 15000
    assert s["close_reason"] == "token_limit"
    assert store.get_active_session("u1") is None
    assert store.get_last_session_summary("u1") == "test"


def test_messages(store):
    sid = store.create_session("u1")
    store.add_message(sid, "user", "hello")
    store.add_message(sid, "assistant", "hi")
    msgs = store.get_session_messages(sid)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"


def test_agent_memory(store):
    store.write_memory("key1", "val1")
    assert store.read_memory("key1") == "val1"
    store.write_memory("key1", "val2")
    assert store.read_memory("key1") == "val2"


def test_notes(store):
    store.add_note("u1", "vegetarian")
    store.add_note("u1", "likes pasta")
    assert len(store.get_notes("u1")) == 2


def test_activities(store):
    store.log_activity("u1", "Pasta", item_id="r1")
    acts = store.get_recent_activities("u1")
    assert acts[0]["item_title"] == "Pasta"


def test_favorites(store):
    store.add_favorite("u1", "i1", "Item 1")
    assert store.is_favorite("u1", "i1")
    store.remove_favorite("u1", "i1")
    assert not store.is_favorite("u1", "i1")


def test_preferences(store):
    store.update_preferences("u1", {"a": 1})
    store.update_preferences("u1", {"b": 2})
    assert store.get_preferences("u1") == {"a": 1, "b": 2}


def test_cron_jobs(store):
    store.get_or_create_user("u1")
    store.add_cron_job("j1", "u1", "0 9 * * *", "morning")
    assert len(store.get_cron_jobs("u1")) == 1
    store.remove_cron_job("j1")
    assert len(store.get_cron_jobs("u1")) == 0


def test_user_context(store):
    store.add_note("u1", "vegetarian")
    store.add_favorite("u1", "r1", "Pizza")
    store.update_preferences("u1", {"spice": "low"})
    ctx = store.get_user_context("u1")
    assert "vegetarian" in ctx
    assert "Pizza" in ctx
    assert "spice" in ctx


# ── User Management ──────────────────────────────────────


def test_list_users(store):
    """list_users returns all users with their channels."""
    store.get_or_create_user("u1", name="Alice")
    store.get_or_create_user("u2", name="Bob")
    store.link_channel("u1", "telegram", "111")

    users = store.list_users()
    assert len(users) == 2
    u1 = next(u for u in users if u["user_id"] == "u1")
    assert u1["name"] == "Alice"
    assert len(u1["channels"]) == 1
    assert u1["channels"][0]["channel"] == "telegram"


def test_get_user_channels(store):
    """get_user_channels returns channel links for a user."""
    store.link_channel("u1", "telegram", "111")
    store.link_channel("u1", "discord", "abc")

    channels = store.get_user_channels("u1")
    assert len(channels) == 2
    channel_names = {c["channel"] for c in channels}
    assert channel_names == {"telegram", "discord"}


def test_delete_user(store):
    """delete_user removes user and channel links."""
    store.get_or_create_user("u1", name="Alice")
    store.link_channel("u1", "telegram", "111")

    assert store.delete_user("u1") is True
    assert not store.user_exists("u1")
    assert store.resolve_user("telegram", "111") is None
    # Deleting non-existent user returns False
    assert store.delete_user("u1") is False


def test_get_channel_link(store):
    """get_channel_link returns token + metadata for user+channel."""
    store.link_channel("u1", "telegram", "123456:ABC_TOKEN")
    store.update_channel_metadata_by_user("u1", "telegram", {"chat_id": 99999})

    link = store.get_channel_link("u1", "telegram")
    assert link is not None
    assert link["channel_user_id"] == "123456:ABC_TOKEN"
    assert link["metadata"]["chat_id"] == 99999


def test_get_channel_link_not_found(store):
    """get_channel_link returns None for unknown user+channel."""
    store.get_or_create_user("u1")
    assert store.get_channel_link("u1", "telegram") is None
    assert store.get_channel_link("nonexistent", "telegram") is None


def test_update_channel_metadata_by_user(store):
    """update_channel_metadata_by_user merges metadata by user_id+channel."""
    store.link_channel("u1", "telegram", "token123")
    store.update_channel_metadata_by_user("u1", "telegram", {"chat_id": 111})
    store.update_channel_metadata_by_user("u1", "telegram", {"username": "ali"})

    link = store.get_channel_link("u1", "telegram")
    assert link["metadata"]["chat_id"] == 111
    assert link["metadata"]["username"] == "ali"

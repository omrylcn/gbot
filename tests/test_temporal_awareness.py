"""Faz 22H — temporal awareness in context + history rendering.

4 wired touchpoints:
- nodes._with_temporal_markers / _humanize_age / _humanize_gap
- ContextBuilder._relative_age (also used by LEARNED FACTS, summary)
- store.get_last_session_meta
- ContextBuilder runtime layer & session_summary block include the
  last-activity gap.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from gbot.agent.context.builder import ContextBuilder
from gbot.agent.nodes import (
    _humanize_age,
    _humanize_gap,
    _parse_iso,
    _with_temporal_markers,
)
from gbot.memory.store import MemoryStore


# ── helpers ─────────────────────────────────────────────────────


def _msg_user(content, ts):
    return HumanMessage(content=content, additional_kwargs={"timestamp": ts})


def _msg_ai(content, ts):
    return AIMessage(content=content, additional_kwargs={"timestamp": ts})


# ── _humanize_* ─────────────────────────────────────────────────


def test_humanize_age_buckets():
    # 'gün' is the natural unit through 13 days; 'hafta' kicks in at 14+.
    assert _humanize_age(30) == "şimdi"
    assert _humanize_age(120) == "2 dakika önce"
    assert _humanize_age(60 * 60 * 3) == "3 saat önce"
    assert _humanize_age(60 * 60 * 24) == "1 gün önce"
    assert _humanize_age(60 * 60 * 24 * 9) == "9 gün önce"
    assert _humanize_age(60 * 60 * 24 * 12) == "12 gün önce"
    assert _humanize_age(60 * 60 * 24 * 16) == "2 hafta önce"
    assert _humanize_age(60 * 60 * 24 * 90) == "3 ay önce"
    assert _humanize_age(60 * 60 * 24 * 400) == "1 yıl önce"


def test_humanize_gap_phrases():
    assert _humanize_gap(45) == "45 saniye geçti"
    assert _humanize_gap(60 * 5) == "5 dakika geçti"
    assert _humanize_gap(60 * 60 * 2) == "2 saat geçti"
    assert _humanize_gap(60 * 60 * 24 * 12) == "12 gün geçti"


def test_parse_iso_tolerant():
    assert _parse_iso(None) is None
    assert _parse_iso("") is None
    assert _parse_iso("not a date") is None
    assert _parse_iso("2026-05-22T14:30:00").year == 2026
    # Z-suffixed ISO works too
    parsed = _parse_iso("2026-05-22T14:30:00Z")
    assert parsed is not None and parsed.tzinfo is None


# ── _with_temporal_markers ──────────────────────────────────────


def test_inline_tag_added_to_messages():
    now = datetime.now()
    ts = (now - timedelta(minutes=5)).isoformat()
    out = _with_temporal_markers([_msg_user("merhaba", ts)])
    assert len(out) == 1
    assert "[" in out[0]["content"] and "merhaba" in out[0]["content"]
    assert "5 dakika önce" in out[0]["content"]


def test_gap_marker_inserted_for_long_silence():
    now = datetime.now()
    twelve_days_ago = (now - timedelta(days=12)).isoformat()
    fresh = now.isoformat()
    out = _with_temporal_markers([
        _msg_user("Zeynep'e mesaj at", twelve_days_ago),
        _msg_ai("Tamam", twelve_days_ago),
        _msg_user("merhaba ben döndüm", fresh),
    ])
    # Synthetic system marker must appear before the latest message
    roles = [m["role"] for m in out]
    assert "system" in roles
    marker = next(m for m in out if m["role"] == "system")
    assert "gün geçti" in marker["content"]
    assert marker["content"].startswith("--- ")


def test_no_gap_marker_when_silence_below_threshold():
    now = datetime.now()
    out = _with_temporal_markers([
        _msg_user("a", (now - timedelta(minutes=10)).isoformat()),
        _msg_user("b", now.isoformat()),
    ])
    assert all(m["role"] != "system" for m in out)


def test_messages_without_timestamp_pass_through():
    plain = HumanMessage(content="legacy")  # no additional_kwargs
    out = _with_temporal_markers([plain])
    assert out[0]["content"] == "legacy"  # no inline tag, no crash


# ── ContextBuilder._relative_age ────────────────────────────────


def test_relative_age_handles_none_and_garbage():
    assert ContextBuilder._relative_age(None) == "yakın zaman"
    assert ContextBuilder._relative_age("") == "yakın zaman"
    assert ContextBuilder._relative_age("garbage") == "yakın zaman"


def test_relative_age_buckets():
    now = datetime.now()
    assert "dakika" in ContextBuilder._relative_age(
        (now - timedelta(minutes=10)).isoformat()
    )
    assert "gün" in ContextBuilder._relative_age(
        (now - timedelta(days=12)).isoformat()
    )
    assert "ay" in ContextBuilder._relative_age(
        (now - timedelta(days=90)).isoformat()
    )


# ── store.get_last_session_meta ─────────────────────────────────


@pytest.fixture
def store(tmp_path):
    return MemoryStore(str(tmp_path / "temporal.db"))


def test_session_meta_returns_none_when_no_closed_session(store):
    store.get_or_create_user("u1", "u1")
    assert store.get_last_session_meta("u1") is None


def test_session_meta_returns_summary_and_timestamps(store):
    store.get_or_create_user("u1", "u1")
    sid = store.create_session("u1", channel="api")
    store.end_session(sid, summary="bir özet", close_reason="manual")
    meta = store.get_last_session_meta("u1")
    assert meta is not None
    assert meta["summary"] == "bir özet"
    assert meta["started_at"] is not None
    assert meta["ended_at"] is not None
